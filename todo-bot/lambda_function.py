import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
from telebot.types import KeyboardButton, InlineKeyboardButton
import os
import logging
import re
import boto3
import json


def lambda_handler(event, context):
    global USERS
    logger.debug("Event: \n%s", json.dumps(event))
    logger.debug("Context: \n%s", context)
    # READ webhook data

    # Object Update in json format.
    # See https://core.telegram.org/bots/api#update
    update = telebot.types.JsonDeserializable.check_json(event["body"])

    # Only work with disabled threaded mode. See https://github.com/eternnoir/pyTelegramBotAPI/issues/161#issuecomment-343873014
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

    # PARSE
    if update.get('callback_query'):
        return handle_callback(bot, update)

    message = update.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    if not USERS:
        USERS = {user['id']: user2name(user)}

    command, main_text = get_command_and_text(message.get('text', ''))

    if not command:
        pass
    elif command == '/users':
        # TODO
        bot.send_message(chat['id'], chat['id'], reply_to_message_id=message['message_id'])
        return RESPONSE_200
    elif command == '/update_id':
        bot.send_message(chat['id'], update['update_id'], reply_to_message_id=message['message_id'])
        return RESPONSE_200
    elif command in ['/mytasks', '/tasks_from_me']:
        to_me = command == '/mytasks'
        task_list = Task.get_tasks(
            to_me=to_me,
            user_id=user['id'],
            task_state=TASK_STATE_TODO
        )
        response = ""
        # if to_me:
        #     response = "My Tasks:\n\n"
        # else:
        #     response = "Tasks from Me:\n\n"
        for task in task_list:
            response += "/t%s:\n%s\n\n" % (task.id, task.description)

        bot.send_message(chat['id'], response, reply_to_message_id=message['message_id'])

        return RESPONSE_200
    elif command.startswith('/t'):
        task_id = int(command[2:])
        task = Task.load_by_id(task_id)
        header = "<i>State: %s</i>" % TASK_STATE_TO_HTML[task.task_state]
        bot.send_message(chat['id'], header, reply_to_message_id=message['message_id'], parse_mode='HTML')
        for label, array in [(None, task.messages), ("Discussion:", task.replies)]:
            if not array:
                continue
            if label:
                bot.send_message(chat['id'], label, reply_to_message_id=message['message_id'])
            for from_chat_id, msg_id in task.messages:
                bot.forward_message(
                    chat['id'],
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                )

        buttons = telebot.types.InlineKeyboardMarkup(row_width=2)
        buttons.add(
            *[InlineKeyboardButton(
                TASK_STATE_TO_HTML[task_state],
                callback_data=encode_callback(ACTION_UPDATE_TASK_STATE, task_state=task_state, task_id=task_id)
            ) for task_state in [
                TASK_STATE_TODO,
                TASK_STATE_DONE,
                TASK_STATE_WAITING,
                TASK_STATE_CANCELED,
            ]
            ])
        bot.send_message(chat['id'], "<i>Update state</i>:", reply_markup=buttons, parse_mode='HTML')
        return RESPONSE_200

    # REPLY
    if not main_text and not any(message.get(key) for key in MEDIA) and not message.get('photo'):
        bot.send_message(chat['id'], "<i>Empty message is ignored</i>", reply_to_message_id=message['message_id'], parse_mode='HTML')
        return RESPONSE_200

    # Add new task
    # TODO: support messages without text
    text = message.get('text')
    # date = message['date']
    task_id = message['update_id'] - MIN_UPDATE_ID
    task = Task(task_id)
    task.messages = [(chat['id'], message['message_id'])]
    task.description = text
    task.save()
    bot.send_message(chat['id'], "<i>Task created:</i> /t%s" % task.id, reply_to_message_id=message['message_id'], parse_mode='HTML')

    return RESPONSE_200


def handle_callback(bot, update):
    callback_query = update.get('callback_query')
    callback = decode_callback(callback_query.get('data'))
    message = callback_query.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    # user = message.get('from')

    if callback['action'] == ACTION_UPDATE_TASK_STATE:
        task_id = callback['task_id']
        task_state = callback['task_state']
        task = Task(task_id, task_state)
        task.save_task_state()
        notification = 'New state for /t%s: %s' % (
            task_id,
            TASK_STATE_TO_HTML[task_state]
        )
        bot.send_message(chat['id'], notification, reply_to_message_id=message['message_id'], parse_mode='HTML')

    return RESPONSE_200


###############################
# CONSTS and global variables #
###############################
FROM_INDEX = 'from_id-task_state-index'
TO_INDEX = 'to_id-task_state-index'

# READ environment variables
BOT_TOKEN = os.environ['BOT_TOKEN']
USERS = os.environ.get('USERS')
if USERS:
    USERS = dict(json.loads(USERS))
DYNAMODB_TABLE_TASK = os.environ.get('DYNAMODB_TABLE_TASK')
DYNAMODB_TABLE_USER = os.environ.get('DYNAMODB_TABLE_USER')
LOG_LEVEL = os.environ.get('LOG_LEVEL')
MIN_UPDATE_ID = int(os.environ.get('MIN_UPDATE_ID', 0))

logger = logging.getLogger()
if LOG_LEVEL:
    logger.setLevel(getattr(logging, LOG_LEVEL))


dynamodb = boto3.client('dynamodb')


RESPONSE_200 = {
    "statusCode": 200,
    "headers": {},
    "body": ""
}
MEDIA = {'sticker': 'send_sticker', 'voice': 'send_voice', 'video': 'send_video', 'document': 'send_document', 'video_note': 'send_video_note'}

TASK_STATE_TODO = 0
TASK_STATE_WAITING = 1
TASK_STATE_DONE = 2
TASK_STATE_CANCELED = 3

# To get emoji code use
# http://www.webpagefx.com/tools/emoji-cheat-sheet/
# and https://pypi.python.org/pypi/emoji
EMOJI_TODO = u'\U0001f4dd'  # emoji.emojize(':memo:', use_aliases=True)
EMOJI_WAITING = u'\U0001f4a4'  # emoji.emojize(':zzz:', use_aliases=True)
EMOJI_DONE = u'\u2705'  # emoji.emojize(':white_check_mark:', use_aliases=True)
EMOJI_CANCELED = u'\u274c'  # emoji.emojize(':x:', use_aliases=True)


TASK_STATE_TO_HTML = {
    TASK_STATE_TODO: " %s ToDo" % EMOJI_TODO,
    TASK_STATE_WAITING: "%s Waiting" % EMOJI_WAITING,
    TASK_STATE_DONE: "%s Done" % EMOJI_DONE,
    TASK_STATE_CANCELED: "%s Canceled" % EMOJI_CANCELED,
}


###########
# HELPERS #
###########
def get_command_and_text(text):
    """split message into command and main text"""
    m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
    if m:
        # group(3) is a bot name
        return m.group(1), m.group(3)
    else:
        return None, text


def user2name(user):
    name = user.get('first_name')
    if user.get('last_name'):
        name += ' ' + user.get('last_name')

    return name


#############
# Callbacks #
#############
ACTION_UPDATE_TASK_STATE = 'u'


def encode_callback(action, task_id=None, task_state=None):
    if action == ACTION_UPDATE_TASK_STATE:
        return '%s_%s_%s' % (
            action,
            task_id,
            task_state,
        )


def decode_callback(data):
    splitted = data.split('_')
    action = splitted.pop(0)
    result = {
        'action': action,
    }
    if action == ACTION_UPDATE_TASK_STATE:
        task_id, task_state = splitted
        return {
            'action': action,
            'task_id': int(task_id),
            'task_state': int(task_state),
        }
    return result

#####################
# DynamoDB wrappers #
#####################


class DynamodbItem(object):
    STR_PARAMS = []
    INT_PARAMS = []
    TABLE = 'to-be-updated'
    PARTITION_KEY = 'id'

    # Reading
    @classmethod
    def load_by_id(cls, id):
        res = dynamodb.get_item(
            TableName=cls.TABLE,
            Key={
                cls.PARTITION_KEY: cls.elem_to_num(id),
            }
        )
        return cls.load_from_dict(res['Item'])

    @classmethod
    def load_from_dict(cls, d):
        logger.debug('load_from_dict: %s', d)
        item = cls()
        for convert, params, key in [(str, cls.STR_PARAMS, 'S'), (int, cls.INT_PARAMS, 'N')]:
            for p in params:
                if d.get(p):
                    setattr(item, p, convert(d[p][key]))

        return item

    # Writing
    @staticmethod
    def elem_to_str(value):
        return {"S": str(value)}

    @staticmethod
    def elem_to_num(value):
        return {"N": str(value)}

    @staticmethod
    def elem_to_array_of_str(value):
        return {"SS": [str(v) for v in value]}

    def to_dict(self):
        res = {}
        for p in self.STR_PARAMS:
            res[p] = self.elem_to_str(getattr(self, p))

        for p in self.INT_PARAMS:
            res[p] = self.elem_to_num(getattr(self, p))

        return res

    def _update(self, AttributeUpdates):
        return dynamodb.update_item(
            TableName=self.TABLE,
            Key={
                self.PARTITION_KEY: Task.elem_to_num(getattr(self, self.PARTITION_KEY))
            },
            AttributeUpdates=AttributeUpdates,
        )

    def save(self):
        return dynamodb.put_item(
            TableName=self.TABLE,
            Item=self.to_dict(),
        )

# DYNAMODB_TABLE_TASK
# Task structure:
#
# {
#   // PRIMARY KEY
#   "user_id": USER_ID,
#
#   "activity": ACTIVITY,
#   "task_id": TASK_ID,
#   "telegram_unixtime": UNIXTIME, // date-time according to data from telegram
#   "unixtime": UNIXTIME, // server date-time
# }
class User(DynamodbItem):
    INT_PARAMS = ['user_id', 'task_id', 'telegram_unixtime', 'unixtime']
    STR_PARAMS = ['activity']

    ACTIVITY_NEW_TASK = 'new_task'

    def __init__(self, user_id):
        self.user_id = user_id

# DYNAMODB_TABLE_TASK
# Task structure:
#
# {
#   // PRIMARY KEY
#   "id": ID, //= update_id - MIN_UPDATE_ID
#
#   // SECONDARY KEY (partition)
#   // index1
#   "from_id": USER_ID,
#
#   // index2
#   "to_id": USER_ID,
#
#   // SECONDARY KEY (sort)
#   "task_state": STATE, // STATE: O=TODO, 1=WAITING, 2=DONE, 3=CANCELED
#
#   // Projected keys
#   "description": "Short representation of the TODO"
#
#   // Normal keys
#   "messages": [CHAT_ID + '_' + MESSAGE_ID], // original messages (forwarded or sent to bot)
#   "replies": [CHAT_ID + '_' + MESSAGE_ID], // discussions
# }


class Task(DynamodbItem):
    STR_PARAMS = ['description']
    INT_PARAMS = ['id', 'from_id', 'to_id', 'task_state']
    CHAT_MSG_PARAMS = ['messages', 'replies']

    def __init__(self, task_id, task_state=TASK_STATE_TODO):
        self.replies = []
        self.description = ''

    # Preparing
    # TODO

    # Reading
    @classmethod
    def load_from_dict(cls, d):
        task = super(Task, cls).load_from_dict(d)

        for ss_param in cls.CHAT_MSG_PARAMS:
            if d.get(ss_param):
                setattr(task, ss_param, [
                    chat_msg.split('_')
                    for chat_msg in d[ss_param]['SS']
                ])
        return cls

    @classmethod
    def get_tasks(cls, to_me=True, user_id=None, task_state=None):
        # Doc: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.query
        args = {
            ':user_id': Task.elem_to_num(user_id)
        }
        if to_me:
            condition = "to_id = :user_id"
            index = TO_INDEX
        else:
            condition = "from_id = :task_state"
            index = FROM_INDEX

        if task_state is None:
            pass
        else:
            condition += " and task_state = :task_state"
            args[':task_state'] = Task.elem_to_num(task_state)

        result = dynamodb.query(
            TableName=DYNAMODB_TABLE_TASK,
            IndexName=index,
            Select='ALL_PROJECTED_ATTRIBUTES',
            KeyConditionExpression=condition,
            ExpressionAttributeValues=args,
        )

        return (cls.load_from_dict(task_dict) for task_dict in result['Items'])

    # Writing
    def to_dict(self):
        res = super(Task, self).to_dict()
        for ss_param in self.CHAT_MSG_PARAMS:
            res[ss_param] = self.elem_to_array_of_str(
                ['%s_%s' % (m[0], m[1]) for m in getattr(self, ss_param)]
            )
        return res

    def save_task_state(self):
        return self._update({
            'task_state': {
                'Action': 'PUT',
                'Value': Task.elem_to_num(self.task_state)
            }
        })
# EOF
