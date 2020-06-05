# Copyright 2020 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import json
import logging
import os
import re
import boto3
from datetime import datetime, timedelta
import time

from pynamodb.models import Model
from pynamodb.attributes import *
from python_dynamodb_lock.python_dynamodb_lock import *

# https://github.com/python-telegram-bot/python-telegram-bot
from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove

DYNAMO_DB_TABLE_NAME = os.getenv("DYNAMO_DB_TABLE_NAME", "opinions-bot")

logger = logging.getLogger()
LOG_LEVEL = os.getenv("LOG_LEVEL")
DEBUG = LOG_LEVEL == "DEBUG"
if LOG_LEVEL:
    level = getattr(logging, LOG_LEVEL)
    logging.basicConfig(format='%(name)s [%(levelname)s]: %(message)s', level=level)

bot = Bot(token=os.getenv('TELEGRAM_TOKEN'))

ADD_YOU_OPINION_MESSAGE="""To add your answer, reply to the original message with the question.

<em>Replying to forwarded message will not affect. Moreover, forwarded message with the questions and answers are frozen forever. You can forward the message for fix current answers<em>"""

UPDATING_POLL_MESSAGE_DELAY = 1 # seconds


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
        set_vote(poll_id, vote_text=message.text, reply=message.message_id)
        return

    command, question = get_command_and_text(message.get('text', ''))
    create_poll(message, question)

def handle_cron(cloudwatch_time):
    dt = datetime.strptime(cloudwatch_time, TIME_FORMAT)
    unixtime = (dt - datetime(1970, 1, 1)).total_seconds()
    # TODO

def handle_callback_query(callback_query):
    data = callback_query.data
    message = callback_query.message
    if not (data and message):
        return
    data = data.split(",")
    if data[0] == "another_vote":
        bot.sendMessage(
            message.chat.id,
            ADD_YOU_OPINION_MESSAGE,
            parse_mode='HTML',
            reply_to_message_id=message.message_id
        )
        return
    if data[0] == "vote":
        set_vote(message2poll_key(message), vote_id=int(data[1]))

def message2poll_key(message):
    return "%s:%s" % (message.chat.id, message.message_id)
def poll2chat_message_ids(poll):
    return poll.key.split(":")

def create_poll(message, question):
    # to_json returns string
    # see https://python-telegram-bot.readthedocs.io/en/stable/telegram.telegramobject.html#telegram.TelegramObject.to_json
    author = json.loads(message.from_user.to_json())
    poll = Poll()
    poll_message = bot.sendMessage(
        message.chat.id,
        poll2text(poll),
        parse_mode='HTML',
        reply_markup=poll2markup(poll),
        reply_to_message_id=message.message_id
    )
    poll.key = message2poll_key(poll_message)
    poll.telegram_datetime = datetime.now()
    poll.telegram_version = 1
    poll.save()

def set_vote(poll_id, vote_id=None, vote_text=None, reply=None):
    poll = TODO
    # TODO: update poll data
    try:
        update_poll_message(poll)
    except DynamoDBLockError as e:
        # Ignore ACQUIRE_TIMEOUT. It most cases it means that we got big queue
        # of workers and can simply kill most of them. For example, we got 100
        # votes in a second, then few of them will get to lock, one of them
        # will update poll-message, while the rest have nothing to do.
        logger.debug("DynamoDBLockError: %s", e.code)
        if e.code != DynamoDBLockError.ACQUIRE_TIMEOUT:
            raise

def update_poll_message(poll):
    # get a reference to the DynamoDB resource
    dynamodb_resource = boto3.resource('dynamodb')

    # create the lock-client
    lock_client = DynamoDBLockClient(dynamodb_resource)
    with lock_client.acquire_lock(
            DYNAMO_DB_TABLE_NAME + ":" + poll.key,
            retry_period=timedelta(seconds=UPDATING_POLL_MESSAGE_DELAY/3),
            retry_timeout=timedelta(seconds=UPDATING_POLL_MESSAGE_DELAY*5),
    ):
        poll.refresh()

        if poll.version == poll.telegram_version:
            # no need to update
            logger.debug("Poll was already updated. Exit")
            return

        since_last_update = (datetime.now() - poll.telegram_datetime).total_seconds()
        if since_last_update < UPDATING_POLL_MESSAGE_DELAY:
            time.sleep(UPDATING_POLL_MESSAGE_DELAY - since_last_update)
            poll.refresh()

        chat_id, message_id = poll2chat_message_ids(poll)
        # Update text
        bot.editMessageText(
            chat_id,
            message_id,
            text=poll2text(poll),
            parse_mode='HTML',
        )
        # Update Markup
        bot.editMessageReplyMarkup(
            chat_id,
            message_id,
            reply_markup=poll2markup(poll),
        )
        poll.telegram_version = poll.version + 1
        poll.telegram_datetime = datetime.now()
        poll.save()

def poll2text(poll):
    msg = []
    msg.append("<em>%s</em>" % poll.question)
    msg.append("")
    total = len(poll.votes)

    user_by_id = dict(
        (user.user_id, user)
        for user in poll.users
    )

    users_by_option_id = {}
    for vote in poll.votes:
        users_by_option_id.setdefault(vote.option_id, [])
        users_by_option_id[vote.option_id].append(user_by_id[vote.user_id])

    for opt in poll.options:
        users = users_by_option_id.get(opt.option_id)
        if not users:
            continue
        users = [user2link(u) for u in users]
        msg.append("* %.1f%% %s — %s" % (100.0 * len(users/total), opt.text, ', '.join(users)))
    return "\n".join(msg)

def poll2markup(poll):
    buttons = []

    opt_by_id = dict(
        (opt.option_id, opt)
        for opt in poll.options
    )
    for vote_rel in poll.votes:
        # make buttons for options with votes
        vote = vote_rel.vote_id
        opt = opt_by_id[vote.option_id]
        buttons.append(InlineKeyboardButton(
            opt.text,
            callback_data=",".join([
                "vote",
                opt.option_id
            ])
        ))

    new_vote_button = InlineKeyboardButton(
        "<em>Add your vote</em>",
        callback_data="another_vote"
    )
    buttons.append(new_vote_button)
    return InlineKeyboardMarkup.from_column(buttons)


class User(MapAttribute):
    user_id = NumberAttribute()
    data = JSONAttribute()

class Option(MapAttribute):
    option_id = NumberAttribute()
    text = UnicodeAttribute()

class Vote(MapAttribute):
    user_id = NumberAttribute()
    option_id = NumberAttribute()

class Poll(Model):
    """
    A DynamoDB User
    """
    class Meta:
        table_name = DYNAMO_DB_TABLE_NAME
    key = UnicodeAttribute(hash_key=True)
    # {
    #   "question": STR,
    #   "votes": [{
    #     "text": STR,
    #     "users": [User]
    #     "id": INT
    #   }],
    #   "author": User,
    # }
    # json = JSONAttribute()
    question = UnicodeAttribute
    author = JSONAttribute()
    options = ListAttribute(of=Option)
    votes = ListAttribute(of=Vote)
    users = ListAttribute(of=User)
    # see https://pynamodb.readthedocs.io/en/latest/optimistic_locking.html
    version = VersionAttribute()
    # information about poll message in telegram
    telegram_version = NumberAttribute()
    telegram_datetime = UnicodeDatetimeAttribute()

def get_command_and_text(text):
    """split message into command and main text"""
    m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
    if m:
        # group(3) is a bot name
        return m.group(1), m.group(3)
    else:
        return None, text

def user2link(user):
    user_id = user.user_id
    name = user2name(user)
    user_link = '<a href="tg://user?id=%s">%s</a>' % (user_id, name)
    return user_link


def user2name(user):
    user = user.data
    if user.get('username'):
        return '@%s' % (user.get('username'))

    name = user.get('first_name')
    if user.get('last_name'):
        name += ' %s' % user.get('last_name')

    return name
