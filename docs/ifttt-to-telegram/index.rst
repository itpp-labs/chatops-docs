===========================================
 Posting notifications in telegram via bot
===========================================

This telegram bot substitutes ifttt's applet "Webhook to Telegram", which may work slowly.


.. contents::
   :local:

Deployment
==========

Create a bot
------------
* In telegram client open `BotFather <https://t.me/botfather>`__
* Send ``/newbot`` command to create a new bot
* Follow instruction to set bot name and get bot token
* Keep your token secure and store safely, it can be used by anyone to control your bot

Prepare zip file
----------------
To make `deployment package <https://docs.aws.amazon.com/lambda/latest/dg/lambda-python-how-to-create-deployment-package.html>`__ execute following commands:

::

    mkdir /tmp/bot
    cd /tmp/bot

    pip3 install pyTelegramBotAPI -t .
    wget https://gitlab.com/itpp/chatops/raw/master/ifttt-to-telegram/lambda_function.py
    zip -r /tmp/bot.zip *


Create Lambda function
----------------------

* Navigate to https://console.aws.amazon.com/lambda/home
* Click *Create function*
* Configure the function as described below

Runtime
~~~~~~~

Use ``Python 3.6``

Function code
~~~~~~~~~~~~~

* Set **Code entry type** to *Upload a .zip file*
* Select ``bot.zip`` file you made

Environment variables
~~~~~~~~~~~~~~~~~~~~~
* ``BOT_TOKEN`` -- the one you got from BotFather
* ``TELEGRAM_CHAT`` -- where to send notification. You can chat id by sending any message to `Get My ID bot <https://telegram.me/itpp_myid_bot>`__
* ``EVENT_<EVENT_NAME>`` -- set response value. Use IFTTT syntax. For example:

   ``EVENT_RED_PULL_REQUEST`` set to value ``PR TESTS are failed: {{Value1}}<br> {{Value2}}``. 

Trigger
~~~~~~~

* **API Gateway**. Once you configure it and save, you will see ``Invoke URL`` under Api Gateway **details** section
* Set the security mechanism for your API endpoint as *Open*

Try it out
==========

Use URL of the following format to replace with your ifttt webhook URL:

``<INVOKE_URL>?event=<EVENT_NAME>``, for example ``https://9ltrkrik2l.execute-api.eu-central-1.amazonaws.com/default/MyLambda/?event=RED_PULL_REQUEST``
