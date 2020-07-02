==============
 Opinions bot
==============

This is a sort of poll bot for telegram, but allows user to set a custom answer.

Try it out here: http://t.me/opinions_matter_bot

.. contents::
   :local:


Workflow
========

The bot works in groups only.

* Someone sends ``/new`` + message, e.g. ``/new what do you think?``  -- start new poll
* Bot responds to the command with a message and attached `Inline buttons <https://core.telegram.org/bots#inline-keyboards-and-on-the-fly-updating>`__ ::

  What do you think?

  *no votes yet*

* Once a user replies to the question-message, the question-message is updated and new buttons are added. For example, after few replies the question-message may look like following::

  What do you think?

  * 33% It's good -- @user1
  * 33% Fine for me -- @user2
  * 33% Super! -- @user3

  3 Opinions

  [ It's good ]
  [ Fine for me ]
  [ Super! ]

* Now other users can use buttons to express their opions or send send a new answer in the same way. Example::


  What do you think?

  * 30% It's good -- @user1, @user5, @user6
  * 20% Fine for me -- @user2, @user9
  * 40% Super! -- @user3, @user7, @user8, @user10
  * 10% It's not just good, it's awersome!!! -- @user4

  10 Opinions

  [ It's good ]
  [ Fine for me ]
  [ Super! ]
  [ It's not just good, it's awersome!!! ]


* If user replies to a forwarded bot message, the bot will respond with the comment that explains how to use the bot.

As you see, the voting can be public only.

Settings
========

On creating AWS Lambda, you would need to set following Environment variables:

* TELEGRAM_TOKEN=<telegram token you got from Bot Father>
* LOG_LEVEL=<LEVEL> -- ``DEBUG``, ``INFO``, etc. Set value to ``DEBUG`` on first run to create dynamodb table.
* DYNAMO_DB_TABLE_NAME -- Optional. By default ``opinions-bot``

Bot source
==========

See https://github.com/itpp-labs/chatops-docs/blob/master/tools/opinions-bot/lambda_function.py

Deployment
==========

Create a bot
------------

https://telegram.me/botfather -- follow instruction to set bot name and get bot token

You may need to activate [privacy mode](https://core.telegram.org/bots#privacy-mode) to allow bot to send hints, when user tries to reply to forwarded message.

Prepare zip file
----------------

To make a `deployment package <https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html>`_ execute following commands::

    mkdir /tmp/bot
    cd /tmp/bot

    pip3 install python-telegram-bot pynamodb python_dynamodb_lock --system -t .
    pip3 install git+https://github.com/whatnick/python_dynamodb_lock.git@90022293bd5afd353aeb309288a75e099cb63779 -t .
    wget https://raw.githubusercontent.com/itpp-labs/chatops-docs/master/tools/opinions-bot/lambda_function.py -O lambda_function.py
    # delete cache files
    find . -iname __pycache__ | xargs rm -rf
    # delete built-in or unused dependencies and files
    rm -rf tornado* docutils* boto* six* s3transfer* dateutil* jmespath* urllib3*
    rm cryptography/hazmat/bindings/_openssl.abi3.so
    zip -r /tmp/bot.zip *

Create Lambda function
---------------------- 

* Navigate to https://console.aws.amazon.com/lambda/home
* Click *Create function*
* Configure the function as described below

Runtime
~~~~~~~

In *AWS: Lambda service*

Use ``Python 3.8``

Permissions (Role)
~~~~~~~~~~~~~~~~~~

In *AWS: IAM service: Policies*

* Create policy of actions for DynamoDB:
  
  * *Service* -- ``DynamoDB``
  * *Action* -- ``All DynamoDB actions``
  * *Resources* -- ``All Resources``

In *AWS: IAM service: Roles*

* Open role attached to the lambda function
* Attach created policies

Function code
~~~~~~~~~~~~~

* Use ``Actions > Upload a .zip file``
* Choose ``bot.zip``

Trigger
~~~~~~~

In *AWS: Lambda service*

* **API Gateway**. Once you configure it and save, you will see ``Invoke URL`` under Api Gateway **details** section

Register webhook at telegram
----------------------------

.. code-block:: sh

    AWS_API_GATEWAY=XXX
    TELEGRAM_TOKEN=XXX
    curl -XPOST https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook --data "url=$AWS_API_GATEWAY" --data "allowed_updates=['message','callback_query']"
