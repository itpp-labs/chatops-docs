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

    client = boto3.client('dynamodb')

    # Only work with disabled threaded mode. See https://github.com/eternnoir/pyTelegramBotAPI/issues/161#issuecomment-343873014
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

    # PARSE
    if update.get('callback_query'):
        return handle_callback(client, bot, update)

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
        result = get_tasks(
            client,
            to_me=to_me,
            user_id=user['id'],
            task_state=TASK_STATE_TODO
        )
        response = ""
        # if to_me:
        #     response = "My Tasks:\n\n"
        # else:
        #     response = "Tasks from Me:\n\n"
        for item_dict in result['Items']:
            item = Item().load_from_dict(item_dict)
            response += "/t%s:\n%s\n\n" % (item.id, item.description)

        bot.send_message(chat['id'], response, reply_to_message_id=message['message_id'])

        return RESPONSE_200
    elif command.startswith('/t'):
        task_id = int(command[2:])
        result = get_task(client, task_id)
        item = Item().load_from_dict(result['Item'])
        header = "<i>State: %s</i>" % TASK_STATE_TO_HTML[item.task_state]
        bot.send_message(chat['id'], header, reply_to_message_id=message['message_id'], parse_mode='HTML')
        for label, array in [(None, item.messages), ("Discussion:", item.replies)]:
            if not array:
                continue
            if label:
                bot.send_message(chat['id'], label, reply_to_message_id=message['message_id'])
            for from_chat_id, msg_id in item.messages:
                bot.forward_message(
                    chat['id'],
                    from_chat_id=from_chat_id,
                    message_id=msg_id,
                )

        buttons = telebot.types.InlineKeyboardMarkup(row_width=2)
        buttons.add(
            *[InlineKeyboardButton(
                TASK_STATE_TO_HTML[state],
                callback_data=encode_callback(ACTION_UPDATE_TASK_STATE, task_state=state, task_id=task_id)
            ) for state in [
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
    item = Item(
        from_user=user,
        messages=[(chat['id'], message['message_id'])],
        date=message['date'],
        update_id=update['update_id'],
        description=text,
    )
    add_task(client, item.to_dict())
    bot.send_message(chat['id'], "<i>Task created:</i> /t%s" % item.id, reply_to_message_id=message['message_id'], parse_mode='HTML')

    return RESPONSE_200


def handle_callback(client, bot, update):
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
        update_task_state(client, task_id, task_state)
        notification = 'New state for /t%s: %s' % (
            task_id,
            TASK_STATE_TO_HTML[task_state]
        )
        bot.send_message(chat['id'], notification, reply_to_message_id=message['message_id'], parse_mode='HTML')

    return RESPONSE_200


##########
# CONSTS #
###########
FROM_INDEX = 'from_id-task_state-index'
TO_INDEX = 'to_id-task_state-index'

# READ environment variables
BOT_TOKEN = os.environ['BOT_TOKEN']
USERS = os.environ.get('USERS')
if USERS:
    USERS = dict(json.loads(USERS))
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE')
LOG_LEVEL = os.environ.get('LOG_LEVEL')
MIN_UPDATE_ID = int(os.environ.get('MIN_UPDATE_ID', 0))

logger = logging.getLogger()
if LOG_LEVEL:
    logger.setLevel(getattr(logging, LOG_LEVEL))


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

# Item structure:
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


def add_task(client, item):
    return client.put_item(
        TableName=DYNAMODB_TABLE,
        Item=item,
    )


def update_task_state(client, task_id, task_state):
    return _update_task(client, task_id, {
        'task_state': {
            'Action': 'PUT',
            'Value': Item.elem_to_num(task_state)
        }
    })


def _update_task(client, task_id, AttributeUpdates):
    return client.update_item(
        TableName=DYNAMODB_TABLE,
        Key={'id': Item.elem_to_num(task_id)},
        AttributeUpdates=AttributeUpdates,
    )


def get_task(client, id):
    return client.get_item(
        TableName=DYNAMODB_TABLE,
        Key={
            'id': Item.elem_to_num(id),
        }
    )


def get_tasks(client, to_me=True, user_id=None, task_state=None):
    # Doc: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.query
    args = {
        ':user_id': Item.elem_to_num(user_id)
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
        args[':task_state'] = Item.elem_to_num(task_state)

    result = client.query(
        TableName=DYNAMODB_TABLE,
        IndexName=index,
        Select='ALL_PROJECTED_ATTRIBUTES',
        KeyConditionExpression=condition,
        ExpressionAttributeValues=args,
    )
    return result


class Item(object):
    STR_PARAMS = ['description']
    INT_PARAMS = ['id', 'from_id', 'to_id', 'task_state']

    # messages = [(chat_id, msg_id)]

    def __init__(self, from_user=None, to_user=None, messages=None, task_state=TASK_STATE_TODO, date=None, update_id=None, description=''):
        self.from_id = from_user and from_user['id']
        self.to_id = to_user and to_user['id']
        if not self.to_id:
            self.to_id = self.from_id
        self.messages = messages
        self.task_state = task_state
        self.date = date
        self.update_id = update_id
        if update_id:
            self.id = update_id - MIN_UPDATE_ID

        self.replies = []
        self.description = description

    def load_from_dict(self, d):
        logger.debug('load_from_dict: %s', d)
        for ss_param in ['messages', 'replies']:
            if d.get(ss_param):
                setattr(self, ss_param, [
                    chat_msg.split('_')
                    for chat_msg in d[ss_param]['SS']
                ])

        for convert, params, key in [(str, self.STR_PARAMS, 'S'), (int, self.INT_PARAMS, 'N')]:
            for p in params:
                if d.get(p):
                    setattr(self, p, convert(d[p][key]))

        return self

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
        res = {
            "messages": self.elem_to_array_of_str(
                ['%s_%s' % (m[0], m[1]) for m in self.messages]
            ),
        }
        for p in self.STR_PARAMS:
            res[p] = self.elem_to_str(getattr(self, p))

        for p in self.INT_PARAMS:
            res[p] = self.elem_to_num(getattr(self, p))

        return res
# EOF
