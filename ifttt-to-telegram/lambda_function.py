import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
import os
import logging


logger = logging.getLogger()

# Set Debug level as environment variable (DEBUG, WARNING, ERROR, CRITICAL, INFO), by default: INFO
logger.setLevel(getattr(logging, os.environ.get('LOGGING_LEVEL', 'INFO')))

RESPONSE_200 = {
    "statusCode": 200,
    "headers": {},
    "body": ""
}


def lambda_handler(event, context):
    logger.debug("Event: \n%s", event)

    # READ environment variables
    BOT_TOKEN = os.environ['BOT_TOKEN']
    TELEGRAM_CHAT = os.environ['TELEGRAM_CHAT']
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

    msg = 'TEST'
    bot.send_message(TELEGRAM_CHAT, msg, parse_mode='HTML')
