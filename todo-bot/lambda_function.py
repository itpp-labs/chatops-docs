import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
from telebot.types import KeyboardButton, InlineKeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, ReplyKeyboardRemove
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

    # PARSE
    if update.get('callback_query'):
        return handle_callback(update)

    message = update.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    text = message.get('text')

    if not USERS:
        USERS = {user['id']: user2name(user)}

    command, main_text = get_command_and_text(message.get('text', ''))

    if command:
        return handle_command(update, message, chat, user, command)

    # Empty message
    if not main_text and not any(message.get(key) for key in MEDIA) and not message.get('photo'):
        bot.send_message(chat['id'], "<i>Empty message is ignored</i>", reply_to_message_id=message['message_id'], parse_mode='HTML')
        return RESPONSE_200

    # Check for recent activity
    user_activity = User.load_by_id(user['id'], chat)
    activity = user_activity and user_activity.activity
    task = None
    task_from_me = None
    task_to_me = None
    if activity and activity != User.ACTIVITY_NONE:
        task = Task.load_by_id(user_activity.task_id)
        task_from_me = user['id'] == task.from_id
        task_to_me = user['id'] == task.to_id
        if not (task_from_me or task_to_me):
            bot.send_message(chat['id'], NOT_FOUND_MESSAGE, parse_mode='HTML')
            return RESPONSE_200

    add_message = False
    reply_text = None
    if user_activity.activity == User.ACTIVITY_NEW_TASK:
        telegram_delta = message.get('date') - user_activity.telegram_unixtime
        if telegram_delta < FORWARDING_DELAY:
            add_message = True
            reply_text = '<i>Message was automatically attached to </i>/t%s' % task.id
    elif user_activity.activity == User.ACTIVITY_ATTACHING:
        add_message = True
        reply_text = '/t%s: <i>new message is attached. Send another message to attach or click /stop_attaching</i>' % task.id

    if add_message:
        # Update previous task instead of creating new one
        task.add_message(message)
        task.update_messages()
    elif user_activity.activity == User.ACTIVITY_DESCRIPTION_UPDATING:
        # Update description
        task.description = text
        task.update_description()
        reply_text = '<i>Description is updated for</i> /t%s' % task.id
    elif user_activity.activity == User.ACTIVITY_ASSIGNING:
        # Update performer
        m = re.match('.* u([0-9]+)$', text)
        new_user_id = int(m.group(1))
        new_user_name = USERS.get(new_user_id) or 'User%' % new_user_id
        task.to_id = new_user_id
        task.update_assigned_to()
        reply_text = '<i>%s is new performer for</i> /t%s' % (new_user_name, task.id)
        if user['id'] != new_user_id:
            # notify new user about the task
            new_user_activity = User.load_by_id(new_user_id, chat)
            if new_user_activity.chat_id:
                bot.send_message(
                    new_user_activity.chat_id,
                    '<i>You got new task from %s:\n</i>/t%s\n%s' % (user2name(user), task.id, task.description),
                    parse_mode='HTML'
                )

    else:
        # Just create new task
        # date = message['date']
        task_id = update['update_id'] - MIN_UPDATE_ID
        task = Task(task_id, user_id=user['id'])
        task.add_message(message)
        task.description = message2description(message)
        task.update()
        reply_text = "<i>Task created:</i> /t%s \n<i>To attach more information use</i> /attach%s" % (task.id, task.id)

    if reply_text:
        bot.send_message(chat['id'], reply_text, reply_to_message_id=message['message_id'], parse_mode='HTML')
    return RESPONSE_200


def handle_command(update, message, chat, user, command):
    reply_text = None
    user_activity = None
    reply_markup = None
    if command == '/users':
        reply_text = json.dumps(USERS)
    elif command == '/update_id':
        reply_text = update['update_id']
    elif command in ['/mytasks', '/tasks_from_me']:
        to_me = command == '/mytasks'
        task_list = Task.get_tasks(
            to_me=to_me,
            user_id=user['id'],
            task_state=TASK_STATE_TODO
        )
        reply_text = ""
        # if to_me:
        #     reply_text = "My Tasks:\n\n"
        # else:
        #     reply_text = "Tasks from Me:\n\n"
        for task in task_list:
            reply_text += "/t%s:\n%s\n\n" % (task.id, task.description)
    else:
        user_activity = User.load_by_id(user['id'], chat)

    if command in ['/stop_attaching', '/cancel']:
        if command == '/stop_attaching':
            reply_text = 'Stopped'
        else:
            reply_text = 'Canceled'
        reply_text = '<i>%s. Send a message to create new Task</i>' % reply_text
        user_activity.activity = User.ACTIVITY_NONE
        user_activity.update_activity()
        reply_markup = ReplyKeyboardRemove()
    elif command.startswith('/attach'):
        task_id = int(command[len('/attach'):])
        user_activity.activity = User.ACTIVITY_ATTACHING
        user_activity.task_id = task_id
        user_activity.update_activity_and_task()
        reply_text = '<i>Send message to attach or click /stop_attaching</i>'
    elif command.startswith('/t'):
        task_id = int(command[2:])
        print_task(message, chat, user_activity, task_id)

    if reply_text:
        bot.send_message(chat['id'], reply_text, reply_to_message_id=message['message_id'], parse_mode='HTML', reply_markup=reply_markup)
    return RESPONSE_200


def handle_callback(update):
    callback_query = update.get('callback_query')
    callback = decode_callback(callback_query.get('data'))
    task_id = callback.get('task_id')
    action = callback.get('action')
    message = callback_query.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    reply_text = None
    reply_markup = None
    user_activity = None
    if action == ACTION_UPDATE_TASK_STATE:
        task = Task.load_by_id(task_id)
        if user['id'] in [task.from_id, task.to_id]:
            task_state = callback['task_state']
            task = Task(task_id, task_state)
            task.update_task_state()
            reply_text = 'New state for /t%s: %s' % (
                task_id,
                TASK_STATE_TO_HTML[task_state]
            )
        else:
            reply_text = NOT_FOUND_MESSAGE
    else:
        user_activity = User.load_by_id(user['id'], chat)

    if action == ACTION_UPDATE_DESCRIPTION:
        reply_text = '/t%s: <i>Send new description or click</i> /cancel' % task_id
        user_activity.activity = User.ACTIVITY_DESCRIPTION_UPDATING
        user_activity.task_id = task_id
        user_activity.update_activity_and_task()
    elif action == ACTION_UPDATE_ASSIGNED_TO:
        reply_text = '/t%s: <i>Send new performer or click</i> /cancel' % task_id
        reply_markup = ReplyKeyboardMarkup(row_width=3)
        reply_markup.add([
            KeyboardButton(
                '%s u%s' % (user_name, user_id)
                for user_id, user_name in USERS.items()
            )
        ])
        user_activity.activity = User.ACTIVITY_ASSIGNING
        user_activity.task_id = task_id
        user_activity.update_activity_and_task()
    if reply_text:
        bot.send_message(chat['id'], reply_text, reply_to_message_id=message['message_id'], parse_mode='HTML', reply_markup=reply_markup)

    return RESPONSE_200


def print_task(message, chat, user_activity, task_id, check_rights=True):
    task = Task.load_by_id(task_id)
    user_id = user_activity.user_id
    if not user_id or user_id not in [task.from_id, task.to_id]:
        logger.info('No access to task %s for user %s, because from_id=%s, to_id=%s', task_id, user_id, task.from_id, task.to_id)
        bot.send_message(chat['id'], NOT_FOUND_MESSAGE, parse_mode='HTML')
        return False

    header = "<i>State: %s</i>" % TASK_STATE_TO_HTML[task.task_state]
    bot.send_message(chat['id'], header, reply_to_message_id=message['message_id'], parse_mode='HTML')
    for from_chat_id, msg_id in task.messages:
        bot.forward_message(
            chat['id'],
            from_chat_id=from_chat_id,
            message_id=msg_id,
        )

    buttons = InlineKeyboardMarkup(row_width=2)
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
    buttons.row_width = 1
    buttons.add(
        *[InlineKeyboardButton(
            text,
            callback_data=encode_callback(action, task_id=task_id)
        ) for action, text in [
            (ACTION_UPDATE_DESCRIPTION, 'Update Description'),
            (ACTION_UPDATE_ASSIGNED_TO, 'Set Performer'),
        ]
        ])
    bot.send_message(chat['id'], "<i>Update Task</i> /t%s:" % task_id, reply_markup=buttons, parse_mode='HTML')

    # Also, reset activity to avoid confusion
    user_activity.activity = User.ACTIVITY_NONE
    user_activity.update_activity()


###############################
# CONSTS and global variables #
###############################
NOT_FOUND_MESSAGE = "<i>Task doesn't exist or you don't have access to it</i>"

FROM_INDEX = 'from_id-task_state-index'
TO_INDEX = 'to_id-task_state-index'

# READ environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
USERS = os.environ.get('USERS')
if USERS:
    USERS = dict(json.loads(USERS))
DYNAMODB_TABLE_TASK = os.environ.get('DYNAMODB_TABLE_TASK')
DYNAMODB_TABLE_USER = os.environ.get('DYNAMODB_TABLE_USER')
LOG_LEVEL = os.environ.get('LOG_LEVEL')
MIN_UPDATE_ID = int(os.environ.get('MIN_UPDATE_ID', 0))
FORWARDING_DELAY = int(os.environ.get('FORWARDING_DELAY', 3))

logger = logging.getLogger()
if LOG_LEVEL:
    logger.setLevel(getattr(logging, LOG_LEVEL))


dynamodb = boto3.client('dynamodb')
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)


RESPONSE_200 = {
    "statusCode": 200,
    "headers": {},
    "body": ""
}
MEDIA = {'sticker': 'send_sticker', 'voice': 'send_voice', 'video': 'send_video', 'document': 'send_document', 'video_note': 'send_video_note'}
MEDIA2DESCRIPTION = [
    ('photo', 'Photo'),
    ('sticker', 'Sticker'),
    ('animation', 'GIF'),
    ('audio', 'Audio Record'),
    ('video', 'Video'),
    ('Venue', 'Address'),
    ('location', 'GEO coordinates'),
    ('video_note', 'Video Message'),
    ('voice', 'Voice Message'),
    ('document', 'File'),
]


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


def message2description(message):
    if message.get('text'):
        return message.get('text')
    for key, text in MEDIA2DESCRIPTION:
        if message.get(key):
            return text


#############
# Callbacks #
#############
ACTION_UPDATE_TASK_STATE = 'us'
ACTION_UPDATE_DESCRIPTION = 'ud'
ACTION_UPDATE_ASSIGNED_TO = 'ua'


def encode_callback(action, task_id=None, task_state=None):
    if action == ACTION_UPDATE_TASK_STATE:
        return '%s_%s_%s' % (
            action,
            task_id,
            task_state,
        )
    else:
        return '%s_%s' % (
            action,
            task_id,
        )


def decode_callback(data):
    splitted = data.split('_')
    action = splitted.pop(0)
    result = {
        'action': action,
    }
    task_id = splitted.pop(0)
    result['task_id'] = int(task_id)
    if action == ACTION_UPDATE_TASK_STATE:
        task_state = splitted.pop(0)
        result['task_state'] = int(task_state)

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
        if not res.get('Item'):
            # New Item
            return cls(id)
        return cls.load_from_dict(res['Item'])

    @classmethod
    def load_from_dict(cls, d):
        logger.debug('%s::load_from_dict: %s', cls, d)
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

    def update(self, *fields):
        d = self.to_dict()
        if not fields:
            return dynamodb.put_item(
                TableName=self.TABLE,
                Item=d,
            )
        else:
            AttributeUpdates = {}
            for f in fields:
                AttributeUpdates[f] = {
                    'Value': d.get(f),
                    'Action': 'PUT',
                }
            return self._update(AttributeUpdates)


# DYNAMODB_TABLE_TASK
# Task structure:
#
# {
#   // PRIMARY KEY
#   "user_id": USER_ID,
#
#   "chat_id": CHAT_ID,  # user's chat with a bot. It's used to send notifications
#   "activity": ACTIVITY,
#   "task_id": TASK_ID,
#   "telegram_unixtime": UNIXTIME, // date-time according to data from telegram
#   "unixtime": UNIXTIME, // server date-time
# }
class User(DynamodbItem):
    INT_PARAMS = ['user_id', 'chat_id', 'task_id', 'telegram_unixtime', 'unixtime']
    STR_PARAMS = ['activity']
    TABLE = DYNAMODB_TABLE_USER
    PARTITION_KEY = 'user_id'

    ACTIVITY_NONE = 'none'  # No activity at the moment
    ACTIVITY_NEW_TASK = 'new_task'  # A message or batch of forwarding messages is being sent to the bot
    ACTIVITY_ATTACHING = 'attaching'  # New messages are attached by command /attach123
    ACTIVITY_DESCRIPTION_UPDATING = 'new_description'  # Waiting for new description after using inline button
    ACTIVITY_ASSIGNING = 'new_performer'  # Waiting for new User to todo selected task. Activated by inline button

    def __init__(self, user_id=0):
        self.user_id = user_id
        self.activity = self.ACTIVITY_NONE
        self.chat_id = 0
        self.task_id = 0
        self.telegram_unixtime = 0
        self.unixtime = 0  # it's not used for now

    def update_activity(self):
        return self.update('activity')

    def update_activity_and_task(self):
        return self.update('activity', 'task_id')

    # Reading
    @classmethod
    def load_by_id(cls, id, chat):
        res = super(User, cls).load_by_id(id)
        chat_id = chat['id']
        if chat['type'] == 'private' and res.chat_id != chat_id:
            res.chat_id = chat_id
            res.update_chat_id()
        return res

    # Writing
    def update_chat_id(self):
        return self.update('chat_id')

# DYNAMODB_TABLE_TASK
# Task structure:
#
# {
#   // PRIMARY KEY
#   "id": ID, //= update_id - MIN_UPDATE_ID
#
#   // SECONDARY KEY (partition)
#   // index1
#   "from_id": USER_ID, // assigned by
#
#   // index2
#   "to_id": USER_ID, // assigned to
#
#   // SECONDARY KEY (sort)
#   "task_state": STATE, // STATE: O=TODO, 1=WAITING, 2=DONE, 3=CANCELED
#
#   // Projected keys
#   "description": "Short representation of the TODO"
#
#   // Normal keys
#   "messages": [CHAT_ID + '_' + MESSAGE_ID],
# }


class Task(DynamodbItem):
    STR_PARAMS = ['description']
    INT_PARAMS = ['id', 'from_id', 'to_id', 'task_state']
    CHAT_MSG_PARAMS = ['messages']
    TABLE = DYNAMODB_TABLE_TASK

    def __init__(self, id=0, task_state=TASK_STATE_TODO, user_id=0):
        self.id = id
        self.task_state = task_state
        self.from_id = user_id
        self.to_id = user_id
        self.description = '<i>New Task</i>'
        self.messages = []

    # Preparing
    def add_message(self, message):
        chat = message.get('chat')
        self.messages.append((chat['id'], message['message_id']))

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
        return task

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
            TableName=cls.TABLE,
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

    def update_task_state(self):
        return self.update('task_state')

    def update_messages(self):
        return self.update('messages')

    def update_description(self):
        return self.update('description')

    def update_assigned_to(self):
        return self.update('to_id')
# EOF
