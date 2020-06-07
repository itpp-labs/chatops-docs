============
 Resend bot
============

.. contents::
   :local:

Description
===========

The general idea is to ask questions within the Telegram group (let's call it *Target group*) and get answers.

The group might be:

* Support Team
* IT Department of your company
* etc.


Technical specification
=======================

Chat ID
-------
 ``/thischat`` -- returns id of current chat. It's used for the identification of the Telegram group.
 ``/myid`` -- returns id of a user

Send message
------------
The are following ways for sending *message-reqest* to the bot:

* In **private chat** with the bot: any message

  * e.g. *hello, how are you?, etc*
* In **another** Telegram group (different from *Target group*): any message that starts with `/` and ends with `@<name_of_the_>bot` is used as a response to the Bot's message

  * e.g. `/hey@super_bot`, `/please@request_bot`, etc.

Get message
-----------
The *Target group* receives a copy of the message with a reference to the sender and the original message itself.

Response
--------
Users response to the bot, in so doing:
 * The copy of the response is sent back to chat, which contains the original message.
 * Response from *Target group* is anonymous by default, but could be customized.

Examples
--------

* In the *original chat* from Ivan ``/hey Answer to the Ultimate Question of Life, the Universe, and Everything?``
* In the *Target group* from @name_of_the_bot: ``<a href="userlink">Ivan</a>: Answer to the Ultimate Question of Life, the Universe, and Everything? *msg:<message1>:<chat>*``
* In the *Target group* from @Deep_thought1:``Anybody knows the answer?``
* In the *Target group* from @Deep_thought2:``Let me think a little bit?``
* In the *Target group* from @Deep_thought1:``?``
* In the *Target group* from @Deep_thought2:``Ok, I found and checked the answer!``
* In the *Target group* from @Deep_thought2: *In reply to Ivan: Answer to the Ultimate Question ...* ``The answer is 42!``
* In the *Original chat* from @name_of_the_bot: ``The answer is 42! *msg:<message2>*``


Deployment
==========

Create a bot
------------
https://telegram.me/botfather -- follow instruction to set bot name and get bot token.

Check your steps:

* Use the /newbot command to create a new bot first.
* The name of the bot must be end witn "bot" (e.g. TetrisBot or tetris_bot).
* Keep your token secure and store safely, it can be used by anyone to control your bot.

Prepare zip file
----------------
To make `deployment package <https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html>`_ execute following commands::

    mkdir /tmp/resend-bot
    cd /tmp/resend-bot

    pip2 install pyTelegramBotAPI -t .
    wget https://raw.githubusercontent.com/itpp-labs/chatops-docs/master/resend-bot/lambda_function.py -O lambda_function.py
    zip -r /tmp/resend_bot_package.zip *

Create Lambda function
----------------------

Runtime
~~~~~~~

Use ``Python 2.7``

Environment variables
~~~~~~~~~~~~~~~~~~~~~
* ``BOT_TOKEN`` -- the one you got from BotFather
* ``TARGET_GROUP`` -- put here Chat ID from the Target group using ``/thischat`` command

  * Note: ID number may contains the "-" before number
* ``ANONYMOUS_REPLY`` -- whether to send replies anonymously. Default True.
* ``AANONYMOUS_REQUEST_FROM_GROUPS`` -- whether to show author name on requesting from another group. Default True.

* ``ACCESS_BOT_LIST`` -- List of ID's (users) which can use the bot. If empty - everyone can.
* ``LOGGING_LEVEL`` -- Level of loger. (Allowed values: DEBUG, INFO, CRITICAL, ERROR, WARNING), by default: INFO

Trigger
~~~~~~~
* **API Gateway**. Once you configure it and save, you will see ``Invoke URL`` under Api Gateway **details** section
* Set the security mechanism for your API endpoint as Open


Register webhook at telegram
----------------------------
* Replace "PASTETHETOKEN" with your Telegram HTTP API access token.
* Replace "PASTEAWSWEBHOOK" with your Invoke URL obtained in the previous section.
* Run following command


via python lib
~~~~~~~~~~~~~~

Execute once in python console::

    BOT_TOKEN = "PASTETHETOKEN"
    WEB_HOOK = "PASTEAWSWEBHOOK"

    import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
    bot.set_webhook(WEB_HOOK, allowed_updates=['message'])

via curl
~~~~~~~~

.. code-block:: sh

    # TODO pass allowed_updates arg
    curl -XPOST https://api.telegram.org/bot<YOURTOKEN>/setWebhook\?url\=YOURAPIGATEWAYURL
