==========
 ToDo bot
==========

.. contents::
   :local:

Description
===========

Allows to create TODOs for a small group of users.

Tasks can have on of the following states:

* TODO -- to be done
* DONE -- done
* CANCELED -- nothing was done and not going to be done
* WAITING -- cannot be started and waits for something

.. warning:: Official telegram docs say that "Bot storage is limited", though it's unknow how much or how long messages are kept in telegram servers. That may cause losing forwarded messages, while bot keeps only message IDS and task's description.

Technical specification
=======================


* ``/mytasks``, ``/tasks_from_me`` -- shows tasks. By default it shows only WAITING and TODO tasks

  * Prints all tasks in a single message with button "Load more Done", "Load more WAITING", "Load more Canceled"
* ``/t123`` -- shows specific task.
   * Prints original forwarded messages
   * You can change status from here

* ``/attach123`` -- attach new messages to a task
* ``/stop_attaching`` -- treat next messages as new task
* ``/assign123`` -- assign a task to another person

.. * ``/users`` -- returns list of Administators for current chat. It's used to specify list of available users to assign the tasks. You may need to activate "All Members Are Admins" option to get list of all users.
* ``/update_id`` -- current update_id. Can be used to set ``MIN_UPDATE_ID`` (see below)
* ``/myid`` -- Id and Name of current user

To create new task:

* Forward message to the bot
* Assign to a user from the list

Deployment
==========

Create a bot
------------

https://telegram.me/botfather -- follow instruction to set bot name and get bot token

Prepare zip file
----------------

To make a `deployment package <https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html>`_ execute following commands::

    mkdir /tmp/todo-bot
    cd /tmp/todo-bot

    pip2 install pyTelegramBotAPI -t .
    wget https://gitlab.com/itpp/chatops/raw/master/todo-bot/lambda_function.py -O lambda_function.py
    zip -r /tmp/todo_bot_package.zip *

Create DynamoDB tables
---------------------

Tasks table
~~~~~~~~~~~
It's used to save tasks

* *Partition key:* ``id`` (number)
* Unmark ``[ ] Use default settings`` checkbox

Add Secondary index:

* *Partition key:* ``from_id`` (number)
* *Sort key:*  ``task_state`` (number)
* *Index name:* ``from_id-task_state-index``
* *Projected attributes:* ``Include`` -- then add field ``to_id``, ``description``, ``telegram_unixtime``

Add another Secondary index:

* *Partition key:* ``to_id`` (number)
* *Sort key:*  ``task_state`` (number)
* *Index name:* ``to_id-task_state-index``
* *Projected attributes:* ``Include`` -- then add field ``from_id``, ``description``, ``telegram_unixtime``

Users table
~~~~~~~~~~~
It's used to save current user activity. For example, if user sends batch of forwarded message, we need to change user status to save all messages to a single task.

* *Partition key:* ``user_id`` (number)

Create Lambda function
----------------------

Runtime
~~~~~~~

Use ``Python 2.7``

Environment variables
~~~~~~~~~~~~~~~~~~~~~

* ``BOT_TOKEN`` -- the one you got from BotFather
* ``USERS`` -- Dictionary of users who can be assigned to a task. Format: ``{USER_ID: USER_NAME}``. At this moment there is no API to get list of members. As a workaround you can ask users to send /myid command to get name and id and prepare the dictionary manually. To use emoji in user names to as following:

   * Get emoji code via http://www.webpagefx.com/tools/emoji-cheat-sheet/
   * Install python lib: https://pypi.python.org/pypi/emoji
   * Prepare json in python console::

     import emoji
     import json
     d = {"123": ":thumbsup: Ivan"}
     print(json.dumps(dict([(k, emoji.emojize(v, use_aliases=True)) for k, v in d.items()])))


* ``DYNAMODB_TABLE_TASK`` -- table with tasks
* ``DYNAMODB_TABLE_USER`` -- table with users
* ``LOG_LEVEL`` -- ``DEBUG`` or ``INFO``
* ``MIN_UPDATE_ID`` -- Number to distract from update_id in task's id computation. Use ``/update_id`` to get value.
* ``FORWARDING_DELAY`` -- max seconds to wait for next forwarded message. It's a
  workaround for limitation of telegram API -- it sends forwarded messages one
  by one and never in a single event. Default is 3 sec.


Trigger
~~~~~~~

User ``API Gateway``. Once you configure it and save, you will see ``Invoke URL`` under Atpi Gateway **details** section

Role
~~~~

* The role must allow access to lambda and dynamodb services. The mimimal policies are:

for dynamodb:

.. code-block:: json

    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "VisualEditor0",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeReservedCapacity*",
                    "dynamodb:List*",
                    "dynamodb:DescribeTimeToLive",
                    "dynamodb:DescribeLimits"
                ],
                "Resource": "*"
            },
            {
                "Sid": "VisualEditor1",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:CreateTable",
                    "dynamodb:BatchGet*",
                    "dynamodb:PutItem",
                    "dynamodb:DescribeTable",
                    "dynamodb:Delete*",
                    "dynamodb:Get*",
                    "dynamodb:BatchWrite*",
                    "dynamodb:Scan",
                    "dynamodb:Query",
                    "dynamodb:DescribeStream",
                    "dynamodb:Update*"
                ],
                "Resource": "arn:aws:dynamodb:*:*:table/*"
            }
        ]
    }

for lambda (created automatically somehow)

.. code-block:: json

    {
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
    }

Register webhook at telegram
----------------------------


via python lib
~~~~~~~~~~~~~~

Execute once in python console::

    BOT_TOKEN = "PASTETHETOKEN"
    WEB_HOOK = "PASTEAWSWEBHOOK"

    import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
    bot.set_webhook(WEB_HOOK)

via curl
~~~~~~~~

.. code-block:: sh

    # TODO pass allowed_updates arg
    curl -XPOST https://api.telegram.org/bot<YOURTOKEN>/setWebhook\?url\=YOURAPIGATEWAYURL
