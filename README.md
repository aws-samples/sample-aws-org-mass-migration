# AWS Account Migration Solution

This solution facilitates the migration of AWS accounts from one organization/payer to another, handling the process in phases with support for migration waves.

## Description
This solution is used to migrate large number of AWS accounts from one organization/payer to another. The code is to be executed (as described in Execution Process) in both current and new payer accounts as a Lambda function (deploy as is) or Python script (conversion needed). The code will automatically detect the current partition and build the appropriate ARNs so you should be able to use the same code for different AWS partitions (ie: commercial, GovCloud...).

## Preparation

1. **Create Lambda Functions**:
   - Deploy one Lambda function in each payer account (current and new)
   - Configure sufficient execution time (accounts take ~2-3 seconds each to process)

2. **Required Lambda Permissions**:
   - CloudWatch access
   - STS (assume role, get caller identity)
   - IAM (role management operations)
   - Organizations (list accounts, manage handshakes, remove/invite accounts)

3. **Service Limits**:
   - Request increases for "Open Invites" and account limits in the new payer
   - Default limits: 10 accounts, 10 open invites (some accounts may have 20)

The solution code is created for a Lambda function as it allows for more flexibility in terms of error logging and debugging. As such, you should create a new Lambda function in each payer account and paste the code in. It should take approximately 2-3 seconds for the Lambda function to process each linked account therefore, be sure to configure Lambda execution time and allow enough time to process all accounts in each migration wave.

Before you start the migration, be sure to review/request service limit increases for the new payer. Get the maximum "Default maximum number of accounts" limit increased as needed. By default, new account/organization has the following limits:
  - Accounts: 10

## Execution Process

The Lambda function can be executed via CLI (see below for more details) or via the console. If you execute it via the console, be sure the create/update the Test Event with the content of the "wave.json" file after you update the required variables.

If you do not want to execute the code as a Lambda function, you can convert the code into a Python script. Make sure the script has access to credentials with appropriate IAM permissions.

If you have a large set of accounts to be transfer, break them up into multiple waves and start with a smaller batch/wave first then gradually increase the size of the batch (ie: crawl, walk & run).

### Phase 1: Group Accounts (Current Payer)
*Optional task to organize accounts into migration waves*
Execute this task if you have a large number of accounts in your organization and want to programmatically query a list of accounts to break them up into more manageable waves.

- **Task**: `group_accounts`
- **Key Parameter**: `wave_len` (accounts per wave)
- **Purpose**: Queries Organizations API to list all accounts and divide into manageable waves.
- **Output**: Creates wave.json file(s) for subsequent phases

### Phase 2: Remove Accounts (Current Payer)
*Prepares and removes accounts from current organization*

This phase uses the "wave.json" file(s) created in Phase 1 as the input. During this phase, the function/script will create/update the required IAM role to grant new payer access to the linked accounts, and removed the accounts from the current organization. By default, the role name is OrganizationAccountAccessRole. Your organization might have a different role name. You can keep the same role name or specify a different one; however, it should match with the role used in the new organization. For Phase 2, you can execute one wave after another until all accounts are removed from the organization. However, we recommend you do one wave at a time in lock step with Phase 3. This gives you the opportunity to fix any issue that might arise and prevent potential issues with billing early on in the process.

- **Task**: `grant_access`
- **Steps**:
  1. Grant access: Creates/updates IAM role for new payer account
- **Input**: Uses wave.json files from Phase 1

### Phase 3: Invite Accounts (New Payer)
*Brings accounts into the new organization*

This phase uses the same "wave.json" file from Phase 2 but the task variable is updated to "invite_accounts". If you have multiple wave files, this phase should to be in lock step with Phase 2 (ie: Phase 2 process wave_1.json then Phase 3 needs to process wave_1.json before you can move on to wave_2.json...). During this phase, the new payer will send invites to the linked accounts, assume role in each account, accept the invite and finally, clean up the role to remove access from the current (or now old) payer. This is where the Open Invites limit comes into play. The code will further break up each wave into smaller batches of 10 or 20 accounts based on the "max_invites" variable to handle the limit.

- **Task**: `invite_accounts`
- **Steps**:
  1. Send invite: Invites accounts to new organization
  2. Accept invite: Uses assume role to accept invites on behalf of accounts
  3. Remove access: Cleans up IAM roles to revoke old payer access
- **Input**: Uses same wave.json files as Phase 2
- **Note**: Processes in batches based on `max_invites` parameter

## Key Parameters

| Parameter | Description |
|-----------|-------------|
| wave_len | Number of accounts per migration wave |
| mgmt_account_id_current | Current payer account ID |
| mgmt_account_id_new | New payer account ID |
| org_access_role_current | Current IAM role for payer access |
| org_access_role_new | New IAM role for payer access |
| max_invites | Open Invites limit (10 or 20) |
| accounts | List of accounts to migrate |
| exclusions | Accounts to exclude from migration |
| task | Operation to perform (group_accounts, remove_accounts, invite_accounts) |

## Execution Methods

### CLI Execution
```bash
aws lambda invoke --function-name Org_Migration --cli-binary-format raw-in-base64-out --payload file://wave.json output.json
```

When you execute the Lambda function via CLI, it will produce the output seen below:
- Without error:
  ```json
  {
      "StatusCode": 200,
      "ExecutedVersion": "$LATEST"
  }
  ```
- With error:
  ```json
  {
      "StatusCode": 200,
      "FunctionError": "Unhandled",
      "ExecutedVersion": "$LATEST"
  }
  ```
- Review the output file to determine the status for each account:
  ```bash
  $ cat output.json
  ```

### Console Execution
Create/update Test Event with the content of wave.json for each migration wave.

If you execute the function in the Lambda console, you will need to create a Test Event with the content of the "wave.json" file. Be sure to update it for each subsequent wave file.

Check CloudWatch Logs for output to determine the status of each account.

## Error Handling

For all accounts that encountered errors, gather them to create a new list or append to another list and rerun. Each action or step during Phase 2 & 3 should provide an account by account error. Follow the guidelines below to check for errors, fix them before rerun.

- **Remove accounts**: There are two steps in this phase.
  - Grant access: If errors occur during this step, check IAM permissions for the current role.
  - Remove accounts: If errors occur during this step, check account contraints (ie: payment method, support plan...).

- **Invite accounts**:
  - Send invite: If errors occur during this step, check IAM permissions for the new role, if the account is valid, or if there is a pending invite.
  - Accept invite: If errors occur during this step, check parameters in the "wave.json" file, check IAM permissions for the new role to see if new payer has permissions.
  - Remove access: If errors occur during this step, check IAM permissions for the new role to see if new payer has permissions.
