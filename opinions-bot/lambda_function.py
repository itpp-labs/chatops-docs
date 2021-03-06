# Copyright 2020 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import json
import logging
import os
import re
import boto3
from datetime import datetime, timedelta, timezone
import time

from pynamodb.models import Model
from pynamodb.attributes import MapAttribute, ListAttribute, NumberAttribute, VersionAttribute, UTCDateTimeAttribute, JSONAttribute, UnicodeAttribute
from python_dynamodb_lock.python_dynamodb_lock import DynamoDBLockClient, DynamoDBLockError

# https://github.com/python-telegram-bot/python-telegram-bot
from telegram import Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
import telegram

bot = Bot(token=os.getenv('TELEGRAM_TOKEN'))
BOT_ID = int(os.getenv('TELEGRAM_TOKEN').split(":")[0])

logger = logging.getLogger("opinions-bot")
LOG_LEVEL = os.getenv("LOG_LEVEL")
DEBUG = LOG_LEVEL == "DEBUG"
if LOG_LEVEL:
    level = getattr(logging, LOG_LEVEL)
    fmt = logging.Formatter('%(name)s [%(levelname)s]: %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger.error("LOG_LEVEL: %s", LOG_LEVEL)

DYNAMO_DB_TABLE_NAME = os.getenv("DYNAMO_DB_TABLE_NAME", "opinions-bot")
ON_RESPONSE_TO_FORWARDED_MESSAGE="""To add your answer, reply to the original bot message, not the forwarded one.

Forwarded message with question and answers are frozen forever. You can forward the message to fix current answers"""

NO_VOTES_YET="""To add your vote, please reply to this message"""

UPDATING_POLL_MESSAGE_DELAY = 1 # seconds
MAX_INLINE_OPTIONS=30

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
        # Create tables
        with CreateTableIfNotExists():
            Poll.create_table(read_capacity_units=5, write_capacity_units=5, wait=True)

        with CreateTableIfNotExists():
            ddb_client = boto3.client('dynamodb')
            DynamoDBLockClient.create_dynamodb_table(ddb_client)

    poll_message = message.reply_to_message
    if poll_message:
        if poll_message.forward_from and poll_message.forward_from.id == BOT_ID:
            # This may happen only is bot has access to all message.
            # Reply to forwarded message, rather than original one.
            bot.sendMessage(
                message.chat.id,
                "<em>%s</em>" % ON_RESPONSE_TO_FORWARDED_MESSAGE,
                parse_mode='HTML',
                reply_to_message_id=message.message_id
            )
        elif  poll_message.from_user.id != BOT_ID:
            # Reply to third-party message.
            # Ignore.
            logger.debug("Reply to third-party message: %s", [BOT_ID, poll_message.from_user.id])
        else:
            poll_key = message2poll_key(poll_message)
            set_vote(message.from_user, poll_key, option_text=message.text, reply=message.message_id)
        return

    command, question = get_command_and_text(message.text or '')
    if not command:
        logger.debug("Message without command")
        return
    if not question:
        logger.debug("Message without text")
        return
    create_poll(message, question)

def handle_cron(cloudwatch_time):
    dt = datetime.strptime(cloudwatch_time, TIME_FORMAT)
    unixtime = (dt - datetime(1970, 1, 1)).total_seconds()
    # This is a placeholder for cron features, e.g. close poll at some point

def handle_callback_query(callback_query):
    user = callback_query.from_user
    data = callback_query.data
    message = callback_query.message
    if not (data and message):
        return
    data = data.split(",")
    if data[0] == "vote":
        set_vote(user, message2poll_key(message), option_id=int(data[1]))

def message2poll_key(message):
    return "%s:%s" % (message.chat.id, message.message_id)
def poll2chat_message_ids(poll):
    return poll.key.split(":")

def telegram2json(telegram_object):
    # to_json returns string
    # see https://python-telegram-bot.readthedocs.io/en/stable/telegram.telegramobject.html#telegram.TelegramObject.to_json
    return json.loads(telegram_object.to_json())

def create_poll(message, question):
    poll = Poll()
    poll.question = question
    poll.author = telegram2json(message.from_user)
    poll_message = bot.sendMessage(
        message.chat.id,
        poll2text(poll),
        parse_mode='HTML',
        reply_markup=poll2markup(poll),
        reply_to_message_id=message.message_id
    )
    poll.key = message2poll_key(poll_message)
    poll.telegram_datetime = get_now()
    poll.telegram_version = 1
    poll.save()

def set_vote(telegram_user, poll_key, option_id=None, option_text=None, reply=None):
    poll = Poll.get(poll_key)
    user_id = str(telegram_user.id)
    if option_text:
        # add option if it doesn't exist yet
        with CheckCondition():
            poll.update(
                actions=[Poll.options.set(
                    Poll.options.append([option_text])
                )],
                condition=~Poll.options.contains(option_text)
            )
        # compute option_id
        for option_id, text in enumerate(poll.options):
            if text == option_text:
                break

    else:
        assert option_id is not None

    try:
        if poll.votes[user_id] == option_id:
            logger.debug("Vote is not changed")
            return
    except KeyError:
        # no vote yet
        pass

    # Add user if it doesn't exist yet.
    db_user = telegram2json(telegram_user)
    with CheckCondition():
        poll.update(
            actions=[Poll.users[user_id].set(db_user)],
            condition=~Poll.users[user_id].exists()
        )

    # Update vote
    poll.update(
        actions=[Poll.votes[user_id].set(option_id)],
    )

    try:
        update_poll_message(poll)
    except DynamoDBLockError as e:
        # Ignore ACQUIRE_TIMEOUT. It most cases it means that we got big queue
        # of workers and can simply kill most of them. For example, we got 100
        # votes in a second, then few of them will get the lock, one of them
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
            raise_context_exception=True,
    ):
        poll.refresh()

        if poll.version == poll.telegram_version:
            # no need to update
            logger.debug("Poll was already updated. Exit")
            return

        now = get_now()
        logger.debug("Checking for sleep: now is %s, last update is %s", now, poll.telegram_datetime)
        since_last_update = (now - poll.telegram_datetime).total_seconds()
        if since_last_update < UPDATING_POLL_MESSAGE_DELAY:
            sleep = UPDATING_POLL_MESSAGE_DELAY - since_last_update
            logger.debug("Sleep for %ssec", poll)
            time.sleep(sleep)
            poll.refresh()

        chat_id, message_id = poll2chat_message_ids(poll)
        # Update text
        try:
            bot.editMessageText(
                poll2text(poll),
                chat_id,
                message_id,
                parse_mode='HTML',
            )
        except telegram.error.BadRequest as e:
            if e.message != "Message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message":
                raise

        # Update Markup
        bot.editMessageReplyMarkup(
            chat_id,
            message_id,
            reply_markup=poll2markup(poll),
        )
        poll.telegram_version = poll.version + 1
        poll.telegram_datetime = get_now()
        poll.save()

def poll2text(poll):
    msg = []
    msg.append("<em>%s</em>" % poll.question)
    msg.append("")
    total = len([v for v in poll.votes])

    users_by_option_id = poll.get_users_by_option_id()

    has_votes = False
    for option_id, option_text in enumerate(poll.options):
        users = users_by_option_id.get(option_id)
        if not users:
            continue
        has_votes = True
        users_links = [user2link(u) for u in users]
        msg.append("* %s — %s <b>%.1f%%</b>" % (
            option_text,
            ', '.join(users_links),
            100.0 * len(users)/total,
        ))
    if not has_votes:
        msg.append("<em>%s</em>" % NO_VOTES_YET)
    logger.debug("poll2text: %s", msg)
    return "\n".join(msg)

def poll2markup(poll):
    buttons = []

    opt_by_id = dict(enumerate(poll.options))

    opt_users = sorted(
        poll.get_users_by_option_id().items(),
        key=lambda item: len(item[1]),
        reverse=True
    )
    i = 0
    for option_id, users in opt_users:
        # make buttons for options with votes
        opt = opt_by_id[option_id]
        buttons.append(InlineKeyboardButton(
            opt,
            callback_data=",".join([
                "vote",
                str(option_id)
            ])
        ))
        i += 1
        if i > MAX_INLINE_OPTIONS:
            break
    return InlineKeyboardMarkup.from_column(buttons)


#class User(MapAttribute):
#    user_id = NumberAttribute()
#    data = JSONAttribute()

#class Option(MapAttribute):
#    option_id = NumberAttribute()
#    text = UnicodeAttribute()

#class Vote(MapAttribute):
#    user_id = NumberAttribute()
#    option_id = NumberAttribute()

class Poll(Model):
    """
    A DynamoDB User
    """
    class Meta:
        table_name = DYNAMO_DB_TABLE_NAME
    key = UnicodeAttribute(hash_key=True)

    question = UnicodeAttribute()
    author = JSONAttribute()
    # option_id is index of the option in the list
    options = ListAttribute(default=list)
    votes = MapAttribute(default=dict)  # user_id -> option_id
    users = MapAttribute(default=dict)   # user_id -> data
    # see https://pynamodb.readthedocs.io/en/latest/optimistic_locking.html
    version = VersionAttribute()
    # information about poll message in telegram
    telegram_version = NumberAttribute()
    telegram_datetime = UTCDateTimeAttribute()

    def get_users_by_option_id(self):
        users_by_option_id = {}
        for user_id in self.votes:
            option_id = self.votes[user_id]
            users_by_option_id.setdefault(option_id, [])
            users_by_option_id[option_id].append(self.users[user_id])
        return users_by_option_id


def get_command_and_text(text):
    """split message into command and main text"""
    m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
    if m:
        # group(3) is a bot name
        return m.group(1), m.group(3)
    else:
        return None, text

def user2link(user):
    user_id = user["id"]
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

class CreateTableIfNotExists():
    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if not exc_type:
            # no exceptions
            return True
        elif exc_value and exc_value.response['Error']['Code'] == "ResourceInUseException":
            # table exists
            return True
        else:
            # reraise error
            return False

class CheckCondition():
    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if not exc_type:
            # no exceptions
            return True
        elif exc_value and exc_value.cause.response['Error']['Code'] == "ConditionalCheckFailedException":
            # Condition evalues to False. That's ok for us
            return True
        else:
            # reraise error
            return False

def get_now():
    return datetime.now(timezone.utc)
