import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
import os
import logging
import json


logger = logging.getLogger()

# Set Debug level as environment variable (DEBUG, WARNING, ERROR, CRITICAL, INFO), by default: INFO
logger.setLevel(getattr(logging, os.environ.get('LOGGING_LEVEL', 'INFO')))

RESPONSE_200 = {
    "statusCode": 200,
    "headers": {},
    "body": ""
}


def lambda_handler(event, context):
    logger.debug("Event: \n%s", json.dumps(event))

    # READ event
    event_name = event['queryStringParameters'].get('event')
    values = json.loads(event['body'])
    logger.debug('event_name=%s; values=%s', event_name, values)

    # READ environment variables
    BOT_TOKEN = os.environ['BOT_TOKEN']
    TELEGRAM_CHAT = os.environ['TELEGRAM_CHAT']
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
    event_template = os.environ.get('EVENT_' + event_name)
    if not event_template:
        return RESPONSE_200

    msg = event_template
    for i in (1,2,3):
        msg = msg.replace('{{Value%i}}' % i, values.get('value%i' % i, ''))
    msg = msg.replace('<br/>', '\n').replace('<br>', '\n')
    bot.send_message(TELEGRAM_CHAT, msg, parse_mode='HTML')
    return RESPONSE_200
