# Copyright 2020 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import json
import logging
import os
import re
import boto3
from datetime import datetime

from pynamodb.models import Model
from pynamodb.attributes import JSONAttribute

# https://github.com/python-telegram-bot/python-telegram-bot
from telegram import Update, Bot, ReplyKeyboardMarkup, ReplyKeyboardRemove

DYNAMO_DB_TABLE_NAME = os.getenv("DYNAMO_DB_TABLE_NAME", "opinions-bot")

logger = logging.getLogger()
LOG_LEVEL = os.getenv("LOG_LEVEL")
DEBUG = LOG_LEVEL == "DEBUG"
if LOG_LEVEL:
    level = getattr(logging, LOG_LEVEL)
    logging.basicConfig(format='%(name)s [%(levelname)s]: %(message)s', level=level)

bot = Bot(token=os.getenv('TELEGRAM_TOKEN'))


def lambda_handler(event, context):
    # read event
    logger.debug("Event: \n%s", json.dumps(event))

    telegram_payload = None
    cloudwatch_time = None
    if event.get("source") == "aws.events":
        cloudwatch_time = event.get('time')
    else:
        telegram_payload = json.loads(event.get("body", '{}'))
        logger.debug("Telegram event: \n%s", telegram_payload)

    # handle event
    try:
        if telegram_payload:
            handle_telegram(telegram_payload)
        elif cloudwatch_time:
            handle_cron(cloudwatch_time)
    except:
        logger.error("Error on handling event", exc_info=True)

    # return ok to telegram server
    return {"statusCode": 200, "headers": {}, "body": ""}

def handle_telegram(telegram_payload):
    update = Update.de_json(telegram_payload, bot)

    if update.callback_query:
        return handle_callback_query(update.callback_query)

    message = update.message
    if not message:
        return

    if message.text == "/start":
        bot.sendMessage(message.chat.id, """This is a poll bot. To create a poll, add this bot to a group and send a poll question with "/new " prefix. For more information check out this page:\nhttps://itpp.dev/chat/opinions-bot/index.html""")
        return

    if DEBUG:
        Storage.create_table(read_capacity_units=1, write_capacity_units=1, wait=True)

    if message.reply_to_message:
        poll_id = message2poll(message.reply_to_message)
        set_opinion(poll_id, text=message.text, reply=message.message_id)
        return

    command, question = get_command_and_text(message.get('text', ''))
    create_poll(message, question)

def handle_cron(cloudwatch_time):
    dt = datetime.strptime(cloudwatch_time, TIME_FORMAT)
    unixtime = (dt - datetime(1970, 1, 1)).total_seconds()
    # TODO

def handle_callback_query(callback_query):
    TODO

def message2poll_id(message):
    return "%s:%s" % (message.message_id, message.chat.id)

def create_poll(message, question):
    # to_json returns string
    # see https://python-telegram-bot.readthedocs.io/en/stable/telegram.telegramobject.html#telegram.TelegramObject.to_json
    author = json.loads(message.from_user.to_json())
    poll = {
        "question": question,
        "opinions": [],
        "author": author,
    }
    poll_message = bot.sendMessage(
        message.chat.id,
        poll2text(poll),
        parse_mode='HTML',
        reply_markup=poll2markup(poll),
        reply_to_message_id=message.message_id
    )
    poll_id = message2poll_id(poll_message)
    s = Storage(poll_id, json=poll)
    s.save()

def set_opinion(poll_id, opinion_id=None, text=None, reply=None):
    TODO

def poll2text(poll):
    msg = []
    msg.append("<em>%s</em>" % poll.get('question'))
    msg.append("")
    total = sum(len(op.users) for op in poll.opinions)
    for op in poll.get('opinions', []):
        users = op.get('users')
        if not users:
            continue
        users = [user2link(u) for u in users]
        msg.append("* %.1f%% %s — %s" % (100.0 * len(users/total), op.text, ', '.join(users)))
    return "\n".join(msg)

def poll2markup(poll):
    TODO


class Storage(Model):
    """
    A DynamoDB User
    """
    class Meta:
        table_name = DYNAMO_DB_TABLE_NAME
    key = UnicodeAttribute(hash_key=True)
    # {
    #   "question": STR,
    #   "opinions": [{
    #     "text": STR,
    #     "users": [User]
    #     "id": INT
    #   }],
    #   "author": User,
    # }
    json = JSONAttribute()
    # see https://pynamodb.readthedocs.io/en/latest/optimistic_locking.html
    version = VersionAttribute()

def get_command_and_text(text):
    """split message into command and main text"""
    m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
    if m:
        # group(3) is a bot name
        return m.group(1), m.group(3)
    else:
        return None, text

def user2link(user):
    user_id = user.get("id")
    name = user2name(user)
    user_link = '<a href="tg://user?id=%s">%s</a>' % (user_id, name)
    return user_link


def user2name(user):
    if user.get('username'):
        return '@%s' % (user.get('username'))

    name = user.get('first_name')
    if user.get('last_name'):
        name += ' %s' % user.get('last_name')

    return name
