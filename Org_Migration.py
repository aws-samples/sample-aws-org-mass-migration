import json
import boto3


partition = ''
this_account = ''
error_msg = []
grant_access_status = []
send_invite_status = []
accept_invite_status = []
remove_access_status = []
phase_2_errors = {}
phase_3_errors = {}
org_client = boto3.client('organizations')
sts_client = boto3.client('sts')


def lambda_handler(event, context):

    # function expects to receive a dictionary that contains info needed to carry out the tasks
    # look for wave.json for an example. Task is set in the json file.
    # Task can be one of the followings:
    # 1. group_accounts - this tasks query accounts from AWS Organizations and organize them in groups
    # 2. grant_access - this task creates/updates the IAM role then remove the accounts from the current org
    # 3. invite_accounts - this task groups the accounts into waves and make the migration more manageable


    global partition, this_account, grant_access_status, send_invite_status, accept_invite_status, remove_access_status, phase_2_errors, phase_3_errors

    response = sts_client.get_caller_identity()
    this_account = response['Account']
    arn_components = response['Arn'].split(':')
    partition = arn_components[1]

    task = get_event_param(event, 'task').lower()
    task_details = ["TASK: " + task]

    error_msg = []

    try:

        # Phase 1: Current payer to execute this task to group accounts together in smaller waves (optional if wave.json is manually generated)
        if task == 'group_accounts':    # this task groups the accounts into waves and make the migration more manageable

            wave_len = get_event_param(event, 'wave_len')
            if wave_len is None:
                wave_len = 100

            waves = make_waves(wave_len)
            if not error_msg:
                return waves

        else:

            account_ids = event['accounts']           
            exclusions = get_event_param(event, 'exclusions')
        
            params = get_event_param(event, 'params')
            mgmt_account_id_current = params['mgmt_account_id_current']
            mgmt_account_id_new = params['mgmt_account_id_new']
            org_access_role_current = params['org_access_role_current']
            org_access_role_new = params['org_access_role_new']


            # breaking up large account list to avoid issue with Open Invites limit
            # max_invites should be set to 10 (default for org) or 20 for most (or higher if increased)
            if 'max_invites' in params:
                max_invites = params['max_invites']
            else:
                max_invites = 10
            account_lists = chunk_list(account_ids, max_invites)
            batch_count = len(account_lists)
            batch_number = 0


            # this loop processes accounts in batches based on Open Invites limit on new Payer Account (see above)
            for account_list in account_lists:
                batch_number += 1
                batch_size = len(account_list)

                task_details.append('BEGIN BATCH ' + str(batch_number) + ' OF ' + str(batch_count) + ' (' + str(batch_size) + ' accounts)')

                # Phase 2: Current payer to execute this task and grant new payer access to the accounts
                if task == 'grant_access':       # this task creates/updates the IAM role then remove the accounts from the current org

                    access_granted = 0
                    excluded_count = 0
                    phase_2_errors = {}

                    grant_access_status = ['GRANT ACCESS...']
                    for member_account_id in account_list:
                        if member_account_id in exclusions:
                            excluded_count += 1
                            grant_access_status.append(member_account_id + ': Excluded.')
                        else:
                            access_granted += grant_access_in_member_account(member_account_id, mgmt_account_id_new, org_access_role_current, org_access_role_new)

                    grant_access_status.append(f'GRANT ACCESS STATUS: (Batch {batch_number}/{batch_count}) -- (Accounts {access_granted}/{batch_size}) -- Excluded {excluded_count} -- Skipped 0.')
                    task_details.append(grant_access_status)

                # Phase 3: New payer to execute this task. Invites the accounts and accepts the invites on behalf of the linked accounts
                elif task == 'invite_accounts':     # this task invites, accepts the accounts into the new org then removes access for old payer

                    if this_account != mgmt_account_id_new:

                        task_details.append('invite_accounts task should only be run in new payer to avoid permission issues.')
                    
                    else:

                        invites_sent = 0
                        invites_accepted = 0
                        access_removed = 0
                        excluded_count = 0
                        phase_3_errors = {}

                        # multiple for loops are not efficient but we leverage them 
                        # below to allow time for API calls to take effect
                        
                        send_invite_status = ['SEND INVITE...']
                        for member_account_id in account_list:
                            if member_account_id in exclusions:
                                excluded_count += 1
                                send_invite_status.append(member_account_id + ': Excluded.')
                            else:
                                invites_sent += send_invite(member_account_id)

                        accept_invite_status = ['ACCEPT INVITE...']
                        remove_access_status = ['REMOVE ACCESS...']
                        for member_account_id in account_list:
                            if member_account_id in exclusions:
                                accept_invite_status.append(member_account_id + ': Excluded.')
                                remove_access_status.append(member_account_id + ': Excluded.')
                            else:

                                if member_account_id in phase_3_errors:
                                    accept_invite_status.append(member_account_id + ': Skipped due to error.  See previous step.')
                                    remove_access_status.append(member_account_id + ': Skipped due to error.  See previous steps.')
                                else:
                                    invites_accepted += accept_invite(member_account_id, mgmt_account_id_new, org_access_role_new)
                                    if member_account_id in phase_3_errors:
                                        remove_access_status.append(member_account_id + ': Skipped due to error.  See previous steps.')
                                    else:
                                        access_removed += remove_access(member_account_id, mgmt_account_id_current, org_access_role_current, org_access_role_new)

                        send_invite_status.append(f'SEND INVITE STATUS: (Batch {batch_number}/{batch_count}) -- (Accounts {invites_sent}/{batch_size}) -- Excluded {excluded_count} -- Skipped 0.')
                        task_details.append(send_invite_status)

                        accept_invite_status.append(f'ACCEPT INVITE STATUS: (Batch {batch_number}/{batch_count}) -- (Accounts {invites_accepted}/{batch_size}) -- Excluded {excluded_count} -- Skipped {len(phase_3_errors)}.')
                        task_details.append(accept_invite_status)

                        remove_access_status.append(f'REMOVE ACCESS STATUS: (Batch {batch_number}/{batch_count}) -- (Accounts {access_removed}/{batch_size}) -- Excluded {excluded_count} -- Skipped {len(phase_3_errors)}.')
                        task_details.append(remove_access_status)

                task_details.append('END BATCH ' + str(batch_number) + ' OF ' + str(batch_count) + ').')

            print(task_details)

    except Exception as e:
        error_msg.append(e)

    if error_msg:
        message = { 'status': 'Task completed.', 'errors': error_msg, 'task details': task_details }
    else:
        message = { 'status': 'Task completed.', 'task details': task_details }
    return message




# return parameter passed in from event
def get_event_param(event, param):

    if param in event:
        return event[param]
    else:
        return None




# get assume role creds for member account
def get_assume_role_creds(member_account_id, role_name):

    global error_msg

    try:

        result = sts_client.assume_role(
            RoleArn = 'arn:' + partition + ':iam::' + member_account_id + ':role/' + role_name,
            RoleSessionName = 'sts_role_to_member_account'
        )
        
        creds = {
            'access_key' : result['Credentials']['AccessKeyId'],
            'secret_key' : result['Credentials']['SecretAccessKey'],
            'session_token' : result['Credentials']['SessionToken']
        }
        
        return creds

    except Exception as e:
        error_msg.append(member_account_id + ' (get_assume_role_creds): ' + str(e))
        return None



# break larger list into smaller ones
def chunk_list(account_list, chunk_size = 10):
    
    return [account_list[i:i + chunk_size] for i in range(0, len(account_list), chunk_size)]




# retrieve all accounts in the org and break them up into waves.
# MaxResults should not be higher than 20 - service limit.
def make_waves(wave_len):

    global error_msg

    try:

        account_list = []
        response = org_client.list_accounts(MaxResults = 20)
        while True:

            accounts = response['Accounts']
            for account in accounts:
                account_list.append(account['Id'])

            # check to see if we have more accounts to query
            if 'NextToken' in response:
                next_token = response['NextToken']
                response = org_client.list_accounts(NextToken = next_token, MaxResults = 20)
            else:
                break

        return { "waves": chunk_list(account_list, wave_len)}

    except Exception as e:
        error_msg.append('make_waves: ' + str(e))




# create/update IAM role in member account before removing it from current org
def grant_access_in_member_account(member_account_id, mgmt_account_id_new, org_access_role_current, org_access_role_new):

    global grant_access_status, phase_2_errors

    try:

        creds = get_assume_role_creds(member_account_id, org_access_role_current)
        iam_client = boto3.client('iam',
            aws_access_key_id = creds['access_key'],
            aws_secret_access_key = creds['secret_key'],
            aws_session_token = creds['session_token'],
        )
        
        # if the same role name is being used then we update the trust policy
        if org_access_role_current == org_access_role_new:
            
            # add new mgmt acct id to current role's trust policy
            response = iam_client.get_role(RoleName = org_access_role_new) 
            trust_policy = response['Role']['AssumeRolePolicyDocument']

            for statement in trust_policy['Statement']:
                principals = statement['Principal']
                aws_principal = principals['AWS']
                if isinstance(aws_principal, str):
                    principals['AWS'] = [ aws_principal ]

                principals['AWS'].append('arn:' + partition + ':iam::' + mgmt_account_id_new + ':root')

                iam_client.update_assume_role_policy(
                    RoleName = org_access_role_current,
                    PolicyDocument = json.dumps(trust_policy)
                )
                grant_access_status.append(member_account_id + ': Grant access - trust policy updated.')

        else:

            # otherwise, create a new role
            trust_policy = {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Effect': 'Allow',
                        'Principal': {
                            'AWS': 'arn:' + partition + ':iam::' + mgmt_account_id_new + ':root'
                    },
                    'Action': 'sts:AssumeRole'
                    }
                ]
            }
    
            iam_client.create_role(
                RoleName = org_access_role_new,
                AssumeRolePolicyDocument = json.dumps(trust_policy),
                Description = 'Organization account access role.'
            )
    
            perm_policy = {
                'Version': '2012-10-17',
                'Statement': {
                    'Action': '*',
                    'Resource': '*',
                    'Effect': 'Allow'
                }
            }
    
            iam_client.put_role_policy(
                RoleName = org_access_role_new,
                PolicyName = 'AdministratorAccess',
                PolicyDocument = json.dumps(perm_policy)
            )
            grant_access_status.append(member_account_id + ': Grant access - new role created.')

        return 1    # for status tracking

    except Exception as e:
        grant_access_status.append(member_account_id + ': Error granting access. ' + str(e))
        phase_2_errors[member_account_id] = member_account_id
        return 0




# invite an account to an organization
def send_invite(member_account_id):

    global send_invite_status, phase_3_errors

    try:

        org_client.invite_account_to_organization(
            Target = {
                'Id': member_account_id,
                'Type': 'ACCOUNT',
            }
        )

        send_invite_status.append(member_account_id + ': Invite sent.')
        return 1    # for status tracking

    except Exception as e:
        send_invite_status.append(member_account_id + ': Error sending invite to account. ' + str(e))
        phase_3_errors[member_account_id]= member_account_id
        return 0



# accept invite on behalf of member account
def accept_invite(member_account_id, mgmt_account_id_new, org_access_role_new):
    
    global accept_invite_status, phase_3_errors
    
    try:

        creds = get_assume_role_creds(member_account_id, org_access_role_new)
        org_client = boto3.client('organizations',
            aws_access_key_id = creds['access_key'],
            aws_secret_access_key = creds['secret_key'],
            aws_session_token = creds['session_token'],
        )
        
        response = org_client.list_handshakes_for_account(
                Filter = {
                  'ActionType': 'INVITE'
                }
            )

        handshakes = response['Handshakes'] 
        for handshake in handshakes:
            # ARN contains account id of the new org
            if mgmt_account_id_new in handshake['Arn']:
                if handshake['State'] == 'OPEN':
                    response = org_client.accept_handshake(HandshakeId = handshake['Id'])
                    break

        accept_invite_status.append(member_account_id + ': Invite accepted.')
        return 1    # for status tracking

    except Exception as e:
        accept_invite_status.append(member_account_id + ': Error accepting invite. ' + str(e))
        phase_3_errors[member_account_id] = member_account_id
        return 0




# verify that new role exist then delete the current role if differ
# from new role; otherwise remove account from principal list
def remove_access(member_account_id, mgmt_account_id_current, org_access_role_current, org_access_role_new):

    global this_account, remove_access_status

    try:

        creds = get_assume_role_creds(member_account_id, org_access_role_new)
        iam_client = boto3.client('iam',
            aws_access_key_id = creds['access_key'],
            aws_secret_access_key = creds['secret_key'],
            aws_session_token = creds['session_token'],
        )

        # if both orgs use the same role then we update the policy
        if org_access_role_current == org_access_role_new:

            # remove account from role's trust policy
            response = iam_client.get_role(RoleName = org_access_role_new)
            trust_policy = response['Role']['AssumeRolePolicyDocument']

            for statement in trust_policy['Statement']:
                principals = statement['Principal']
                aws_principals = principals['AWS']

                # loop through the list of principals
                # see if there is an entry with new account id
                # if so, delete the entry with old account id
                ok_to_delete = False
                for principal in aws_principals:
                    if this_account in principal:
                        ok_to_delete = True
                        break

                if ok_to_delete:
                    aws_principals.remove('arn:' + partition + ':iam::' + mgmt_account_id_current + ':root')

            iam_client.update_assume_role_policy(
                RoleName = org_access_role_current,
                PolicyDocument = json.dumps(trust_policy)
            )
            remove_access_status.append(member_account_id + ': Remove access for old payer - trust policy updated.')

        else: # 2 orgs use 2 different policies

            # check to see if the new role exist
            response = iam_client.get_role(RoleName = org_access_role_new)
            if isinstance(response, dict):

                # delete/detach policies then delete the role
                response = iam_client.get_role(RoleName = org_access_role_current)

                if isinstance(response, dict):

                    # Detach all policies attached to the current role 
                    attached_policies = iam_client.list_attached_role_policies(RoleName = org_access_role_current) 
                    for policy in attached_policies['AttachedPolicies']: 
                        iam_client.detach_role_policy(RoleName = org_access_role_current, PolicyArn=policy['PolicyArn']) 
        
                    # Remove all inline policies from current role 
                    inline_policies = iam_client.list_role_policies(RoleName = org_access_role_current) 
                    for policy_name in inline_policies['PolicyNames']: 
                        iam_client.delete_role_policy(RoleName = org_access_role_current, PolicyName = policy_name) 
        
                    # delete the current role 
                    iam_client.delete_role(RoleName = org_access_role_current)
                
                    remove_access_status.append(member_account_id + ': Remove access for old payer - old role deleted.')

        return 1    # for status tracking

    except Exception as e:
        remove_access_status.append(member_account_id + ': Error removing access for old payer. ' + str(e))
        return 0
