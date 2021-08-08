=============
 Archive bot
=============

.. contents::
   :local:

Description
===========

The bot backs up files sent via Telegram.

* Back up files and media forwarded or sent to the bot
* Search by tags
* S3 storage
* Attach Unique Identifier to the file. It helps to reference to the file
  outside of telegram without downloading it. For example you can send a message
  in your Task Management tool: *For one who missed the meeting, check recorded
  video in our telegram group: ujp6r5UMNnWAHL5HNLwp4Ss2AOUDFu8*.
* Private and Public mode:

  * Private mode: S3 credentials are preconfigured. Only listed users can use the bot.
  * Public mode: Any person can use it. S3 credentials are provided by users. You can try it here: http://t.me/ArchivisteBot

.. note:: The bot can work even without S3 storage, but at this case the files would not have backup and may be lost:

   * If you clear chat history
   * In case of account deleting or [self-desctruction](https://telegram.org/faq#q-how-does-account-self-destruction-work)
   * Any problems on telegram side

Technical specification
=======================

* Forward or send a file to the bot to create backup
* To make a search simply send search request as a message

Deployment
==========

Create a bot
------------

https://telegram.me/botfather -- follow instruction to set bot name and get bot token

Prepare zip file
----------------

To make a `deployment package <https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html>`_ execute following commands::

    mkdir /tmp/bot
    cd /tmp/bot

    pip3 install python-telegram-bot -t .
    wget https://github.com/itpp-labs/chatops-docs/raw/master/archive-bot/lambda_function.py -O lambda_function.py
    zip -r /tmp/bot.zip *

Create DynamoDB tables
----------------------

TODO

Create Lambda function
---------------------- 

Runtime
~~~~~~~

In *AWS: Lambda service*

Use ``Python 3.7``

Environment variables
~~~~~~~~~~~~~~~~~~~~~

In *AWS: Lambda service*

* ``BOT_TOKEN`` -- the one you got from BotFather
* ``USERS`` -- Optional. Comma-separated list of users who can uses the bot. To get user id, you can use [My ID bot](https://t.me/itpp_myid_bot). When it's not set any user can use the bot.
* ``COMMON_S3_BUCKET`` -- Optional. Set bucket that will be used to back up files for all users. Access is provided by Lambda's Role.
* ``DYNAMODB_TABLE_CODE`` -- prefix for table names.
* ``LOG_LEVEL`` -- e.g. ``DEBUG`` or ``INFO``

Trigger
~~~~~~~

In *AWS: Lambda service*

* **API Gateway**. Once you configure it and save, you will see ``Invoke URL`` under Api Gateway **details** section

Role
~~~~

In *AWS: IAM (Identity and Access Management) service: Policies*

* Create policy of actions for DynamoDB:
  
  * *Service* -- ``DynamoDB``
  * *Action* -- ``All DynamoDB actions``
  * *Resources* -- ``All Resources``

* Create policy of actions for S3 (only if you use COMMON_S3_BUCKET):

  * TODO


TODO: check the rest doc of this section

In *AWS: IAM service: Roles*

In list of roles choose the role, which was named in process of creating lambda function, and attach to it recently created policy for DynamoDB

* The role must allow access to lambda and dynamodb services.

By the final, role should look something like this:

In *AWS: Lambda service: Designer: View Permissions (Key-Icon)*

.. code-block:: json

    {
        
         "roleName": "{ROLE_NAME}",
          "policies": [
            {
              "document": {
                "Version": "2012-10-17",
                "Statement": [
                  {
                    "Effect": "Allow",
                    "Action": [
                      "logs:CreateLogGroup",
                      "logs:CreateLogStream",
                      "logs:PutLogEvents"
                    ],
                    "Resource": [
                      "arn:aws:logs:*:*:*"
                    ]
                  }
                ]
              },          
              "name": "AWSLambdaEdgeExecutionRole-daf8b371-4fc9-4e1a-9809-fcd44b96d4f2",
              "id": "ANPAX7765LQXBC72HXN4W",
              "type": "managed",
              "arn": "arn:aws:iam::549753543726:policy/service-role/AWSLambdaEdgeExecutionRole-daf8b371-4fc9-4e1a-9809-fcd44b96d4f2"
              },
            {
              "document": {
                "Version": "2012-10-17",
                "Statement": [
                  {
                    "Sid": "VisualEditor0",
                    "Effect": "Allow",
                    "Action": "dynamodb:*",
                    "Resource": "*"
                  }
                ]
              },
              "name": "{NAME_OF_POLICY_FOR_DYNAMODB}",
              "id": "ANPAX7765LQXJUGC2FXMV",
              "type": "managed",
              "arn": "arn:aws:iam::549753543726:policy/{NAME_OF_POLICY_FOR_DYNAMODB}"
            }
          ],
          "trustedEntities": [
            "edgelambda.amazonaws.com",
            "lambda.amazonaws.com"
          ]
            
    }


Timeout
~~~~~~~

in *AWS: Lambda service*

Execution time depends on telegram servers and file size. So, think about 60 seconds for limit.

TODO: test with maximum allowed file
Register webhook at telegram
----------------------------

.. code-block:: sh

    # TODO pass allowed_updates arg
    curl -XPOST https://api.telegram.org/bot<YOURTOKEN>/setWebhook\?url\=YOURAPIGATEWAYURL
