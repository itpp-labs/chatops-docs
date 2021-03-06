# -*- coding: utf-8 -*-
# Copyright 2019 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
from telebot.types import KeyboardButton, InlineKeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, ReplyKeyboardRemove
from telebot.apihelper import ApiException

import os
import logging
import re
import boto3
import json
from datetime import datetime


def lambda_handler(event, context):
    global USERS
    global message
    global update
    global chat
    global user
    logger.debug("Event: \n%s", json.dumps(event))
    logger.debug("Context: \n%s", context)
    # Check for cron
    if event.get("source") == "aws.events":
        return handle_cron(event)

    # READ webhook data

    # Object Update in json format.
    # See https://core.telegram.org/bots/api#update
    update = telebot.types.JsonDeserializable.check_json(event["body"])

    # PARSE
    if update.get('callback_query'):
        return handle_callback()

    message = update.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    text = message.get('text')

    command, main_text = get_command_and_text(message.get('text', ''))

    if command:
        return handle_command(command)

    # Empty message
    if not main_text and not any(message.get(key) for key in MEDIA) and not message.get('photo'):
        send("<i>Empty message is ignored</i>", reply=True)
        return RESPONSE_200

    # Check for recent activity
    user_activity = User.load_by_id(user['id'], chat)
    activity = user_activity and user_activity.activity
    task = None
    task_from_me = None
    task_to_me = None
    if activity and activity != User.ACTIVITY_NONE:
        # In case of concurency we raise error to force telegram resend the
        # message when task is not saved by another process yet
        task = Task.load_by_id(user_activity.task_id, raise_if_not_found=True)
        task_from_me = user['id'] == task.from_id
        task_to_me = user['id'] == task.to_id
        if not (task_from_me or task_to_me):
            bot.send_message(chat['id'], NOT_FOUND_MESSAGE, parse_mode='HTML')
            return RESPONSE_200

    add_message = False

    if user_activity.activity == User.ACTIVITY_NEW_TASK:
        telegram_delta = abs(message.get('date') - user_activity.telegram_unixtime)
        logger.debug('telegram_delta=%s message\'s date: %s', telegram_delta, message.get('date'))
        # Share button in iOS allows send couple of messages as a batch
        second_message = len(task.messages) == 1
        if (message.get('forward_from') or second_message) and telegram_delta < FORWARDING_DELAY:
            # automatically attached series of message, but only forwarded or media messages
            add_message = True
            send('<i>%s Message was automatically attached to </i>/t%s' % (EMOJI_AUTO_ATTACHED_MESSAGE, task.id))
            if user_activity.telegram_unixtime < message.get('date'):
                user_activity.telegram_unixtime = message.get('date')
                user_activity.update_time()
    elif user_activity.activity == User.ACTIVITY_ATTACHING:
        add_message = True
        buttons = InlineKeyboardMarkup(row_width=1)
        buttons.add(button_stop_attaching())
        send('%s /t%s: <i>new message is attached. Send another message to attach</i>' % (EMOJI_ATTACHED_MESSAGE, task.id),
             buttons)

    if add_message:
        # Update previous task instead of creating new one
        task.add_and_update_messages(message)
    elif user_activity.activity == User.ACTIVITY_DESCRIPTION_UPDATING:
        # Update description
        buttons = InlineKeyboardMarkup(row_width=1)
        buttons.add(
            button_my_tasks()
        )
        send('<i>Description is updated for</i> /t%s' % task.id, buttons)
        old_description = task.description
        task.description = text
        task.update_description()
        user_activity.activity = User.ACTIVITY_NONE
        user_activity.update_activity()

        notify_another_user(
            task,
            '<b>%s Task Description is updated by</b> %s\n\n<b>NEW:</b> %s\n\n<b>OLD:</b> %s' % (
                EMOJI_NEW_DESCRIPTION_FROM_ANOTHER,
                user2link(user),
                escape_html(task.description),
                escape_html(old_description)
            )
        )

    elif user_activity.activity == User.ACTIVITY_ASSIGNING:
        # Update performer
        m = re.match('.* u([0-9]+)$', text)
        if not m:
            send('<i>Something went wrong. Try again</i>')
            user_activity.activity = User.ACTIVITY_NONE
            user_activity.update_activity()
            return RESPONSE_200
        new_user_id = int(m.group(1))
        new_user_name = user_id2name(new_user_id)

        reply_text = '<i>%s is new performer for</i> /t%s' % (new_user_name, task.id)
        reply_markup = ReplyKeyboardRemove()
        send(reply_text, reply_markup)

        task.to_id = new_user_id
        task.update_assigned_to()

        user_activity.activity = User.ACTIVITY_NONE
        user_activity.update_activity()

        if user['id'] != new_user_id:
            # notify new user about the task
            new_user_activity = User.load_by_id(new_user_id)
            if new_user_activity.chat_id:
                bot.send_message(
                    new_user_activity.chat_id,
                    '<i>%s You got new task from</i> %s:\n/t%s\n%s' % (
                        EMOJI_NEW_TASK_FROM_ANOTHER,
                        user2link(user),
                        task.id,
                        escape_html(task.description),
                    ),
                    parse_mode='HTML'
                )

    elif str(user['id']) not in USERS:
        send('<i>It\'s a private bot, sorry. But you can create new one for yourself: </i> https://chatops.readthedocs.io/en/latest/todo-bot/index.html')
    else:
        # Just create new task
        task_id = update['update_id'] - MIN_UPDATE_ID
        buttons = task_bottom_buttons(task=None, task_id=task_id)
        send("<i>{emoji} Task created:</i> /t{task_id}".format(emoji=EMOJI_NEW_TASK, task_id=task_id),
             buttons)
        # It's important to update activity first, because second message in a
        # batch can be proceeded by another process, so we have to update activity ASAP.
        # Though, there is no gurantee that this process will do it faster.
        # A strong solution is avoiding concurency via Lambda config "Reserve concurrency = 1"
        # OR by making FIFO queue per user
        # OR using lock in user activity
        user_activity.activity = User.ACTIVITY_NEW_TASK
        user_activity.task_id = task_id
        user_activity.telegram_unixtime = message.get('date')
        user_activity.update_activity_task_time()

        task = Task(task_id, user_id=user['id'])
        task.add_message(message)
        task.description = message2description(message)
        task.telegram_unixtime = message.get('date')
        task.next_reminder = message.get('date') + 24 * 3600 * REMINDER_DAYS
        task.update()
    return RESPONSE_200


def handle_command(command):
    user_activity = None
    # commands without_activity
    if command == '/start':
        send('<i>Send or Forward a message to create new task</i>')
        # create User record (see User.load_by_id method)
        user_activity = User.load_by_id(user['id'], chat)
    elif command == '/users':
        # Apparently, there is no way to get list of users
        pass
        # members = bot.get_chat_administrators(chat['id'])
        # logger.debug('get_chat_administrators response: %s', members)
        # result = {}
        # for m in members:
        #     user = m.user
        #     user_dict = {
        #         'first_name': user.first_name,
        #         'last_name': user.last_name,
        #         'username': user.username,
        #     }
        #     result[user['id']] = user2name(user_dict)
        # reply_text = json.dumps(result)
    elif command == '/myid':
        send(json.dumps({user['id']: user2name(user)}))
    elif command == '/update_id':
        send(update['update_id'])
    elif command in ['/mytasks', '/tasks_from_me']:
        # TODO: make cache for /mytasks
        to_me = command == '/mytasks'
        com_tasks(to_me)
    elif re.match('/t[0-9]+', command):
        task_id = int(command[2:])
        com_print_task(task_id)
    else:
        user_activity = User.load_by_id(user['id'], chat)

    if command in ['/stop_attaching', '/cancel']:
        cancel = command == '/cancel'
        com_cancel(user_activity, cancel)
    elif command.startswith('/attach'):
        task_id = int(command[len('/attach'):])
        com_attach(user_activity, task_id)
    elif command.startswith('/assign'):
        task_id = int(command[len('/assign'):])
        com_assign(user_activity, task_id)
    return RESPONSE_200


def handle_callback():
    callback_query = update.get('callback_query')
    callback = decode_callback(callback_query.get('data'))
    task_id = callback.get('task_id')
    action = callback.get('action')
    global message
    message = callback_query.get('message')
    if not message:
        return RESPONSE_200

    global chat
    chat = message.get('chat')

    # message's "from" is Bot User, not the User who clicked the inline button
    global user
    user = callback_query.get('from')

    user_activity = None
    # actions without activity
    if action == ACTION_UPDATE_TASK_STATE:
        # TODO: update buttons where is was clicked
        com_update_task_state(task_id, callback['task_state'])
    elif action == ACTION_MY_TASKS:
        com_tasks(header='<b>My Tasks</b>', reply=False)
    elif action == ACTION_TASKS_FROM_ME:
        com_tasks(to_me=False, header='<b>Tasks From Me</b>', reply=False)
    elif action == ACTION_TASK:
        com_print_task(task_id)
    else:
        user_activity = User.load_by_id(user['id'], chat)

    if action == ACTION_UPDATE_DESCRIPTION:
        com_update_description(user_activity, task_id)
    elif action == ACTION_UPDATE_ASSIGNED_TO:
        com_update_assigned_to(user_activity, task_id)
    elif action == ACTION_ATTACH_MESSAGES:
        com_attach(user_activity, task_id)
    elif action in [ACTION_CANCEL, ACTION_STOP]:
        cancel = action == ACTION_CANCEL
        com_cancel(user_activity, cancel, reply=False)

    return RESPONSE_200


def handle_cron(event):
    # Time example "2019-04-16T09:45:07Z"
    TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
    NOTIFICATION_TITLE = u'\u2757\ufe0f' + "There are some old tasks. Please, either <b>do</b> them, <b>relocate</b> somewhere or mark as <b>canceled</b>."
    time = event['time']
    dt = datetime.strptime(time, TIME_FORMAT)
    unixtime = (dt - datetime(1970, 1, 1)).total_seconds()

    for user_id, user_name in USERS.items():
        user_tasks = list(Task.get_tasks_to_remind(user_id, unixtime))
        has_tasks = len(user_tasks)
        if has_tasks:
            user_activity = User.load_by_id(user_id)
            try:
                bot.send_message(
                    user_activity.chat_id,
                    NOTIFICATION_TITLE,
                    parse_mode='HTML',
                )
            except:
                # chat not found?
                continue

        for task in user_tasks:
            # FIXME: the code is copy-pasted
            reply_text = '%s\n\n%s' % (
                escape_html(task.description),
                task_summary(task, user_id)
            )
            reply_markup = task_state_keyboard(task, row_width=4)
            bot.send_message(
                user_activity.chat_id,
                reply_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )

            task.next_reminder = unixtime + 24 * 3600 * REMINDER_DAYS
            task.update_next_reminder()

        if has_tasks:
            bot.send_message(
                user_activity.chat_id,
                NOTIFICATION_TITLE,
                parse_mode='HTML',
            )
    return RESPONSE_200


def com_update_assigned_to(user_activity, task_id):
    buttons = InlineKeyboardMarkup(row_width=1)
    buttons.add(button_cancel())
    send('/t%s' % task_id, assign_keyboard())
    send('<i>Send new performer</i>', buttons)

    user_activity.activity = User.ACTIVITY_ASSIGNING
    user_activity.task_id = task_id
    user_activity.update_activity_and_task()


def com_update_description(user_activity, task_id):
    buttons = InlineKeyboardMarkup(row_width=1)
    buttons.add(button_cancel())
    send('/t%s: <i>Send new description or click</i> /cancel' % task_id, buttons)
    user_activity.activity = User.ACTIVITY_DESCRIPTION_UPDATING
    user_activity.task_id = task_id
    user_activity.update_activity_and_task()


def com_update_task_state(task_id, task_state):
    task = Task.load_by_id(task_id)

    if task.task_state != task_state:
        if user['id'] in [task.from_id, task.to_id]:

            task.task_state = task_state
            task.update_task_state()
            update_task_message_text(task,message['message_id'], user['id'])

            notify_another_user(
                    task,
                    '<b>%s Task State is changed by</b> %s\n\n%s' % (
                    EMOJI_NEW_STATE_FROM_ANOTHER,
                    user2link(user),
                    escape_html(task.description)
                    )
                    )
        else:
            send(NOT_FOUND_MESSAGE)


def com_assign(user_activity, task_id):
    reply_markup = assign_keyboard()
    send('<i>Select new performer or click /cancel</i>', reply_markup)
    user_activity.activity = User.ACTIVITY_ASSIGNING
    user_activity.task_id = task_id
    user_activity.update_activity_and_task()


def com_attach(user_activity, task_id):
    buttons = InlineKeyboardMarkup()
    buttons.add(button_stop_attaching())
    send('<i>%s Send message to attach</i>' % EMOJI_SEND_MESSAGE_TO_ATTACH, buttons)
    user_activity.activity = User.ACTIVITY_ATTACHING
    user_activity.task_id = task_id
    user_activity.update_activity_and_task()


def com_cancel(user_activity, cancel=True, reply=True):
    if cancel:
        reply_text = 'Request for input is canceled'
    else:
        reply_text = 'Stopped'
    send('%s <i>%s</i>' % (EMOJI_ATTACHING_STOPPED, reply_text), ReplyKeyboardRemove(), reply=reply)

    buttons = InlineKeyboardMarkup(row_width=1)
    buttons.add(button_my_tasks())
    reply_text = '<i>Send a message to create new Task</i>'
    send(reply_text, buttons, reply=False)
    user_activity.activity = User.ACTIVITY_NONE
    user_activity.update_activity()


def com_tasks(to_me=True, header=None, reply=True):
    user_id = user['id']
    task_list = Task.get_tasks(
        to_me=to_me,
        user_id=user_id,
        task_state=TASK_STATE_TODO
    )
    if header:
        send(header, reply_markup=ReplyKeyboardRemove(), reply=reply)

    not_found = True
    for task in task_list:
        # The problem with buttons is that when you click them, telegram doesn't scroll down on new messages
        # buttons = InlineKeyboardMarkup(row_width=1)
        # buttons.add(button_task(task, user_id))
        reply_text = '%s\n\n%s' % (
            escape_html(task.description),
            task_summary(task, user_id)
        )
        reply_markup = task_state_keyboard(task, row_width=4)
        send(reply_text, reply=False, reply_markup=reply_markup)
        not_found = False

    if not_found:
        send("<i>Tasks are not found</i>", reply=reply)


def com_print_task(task_id, check_rights=True):
    task = Task.load_by_id(task_id)
    user_id = user['id']
    if not user_id or user_id not in [task.from_id, task.to_id]:
        logger.info('No access to task %s for user %s, because from_id=%s, to_id=%s', task_id, user_id, task.from_id, task.to_id)
        bot.send_message(chat['id'], NOT_FOUND_MESSAGE, parse_mode='HTML', reply_markup=ReplyKeyboardRemove())
        return False

    header = escape_html(task.description)
    header += '\n\n'
    header += task_summary(task, user_id)
    buttons = InlineKeyboardMarkup(row_width=2)
    buttons.add(
        button_update_description(task_id),
    )

    send(header, reply=True, reply_markup=buttons)
    for from_chat_id, msg_id in task.messages:
        try:
            bot.forward_message(
                chat['id'],
                from_chat_id=from_chat_id,
                message_id=msg_id,
            )
        except ApiException as e:
            res = e.result.json()
            if res['description'] == "Bad Request: message to forward not found":
                send("Message is not found. The sender has probably deleted bot's chat history: msg_id=%s" % msg_id)            

    buttons = task_bottom_buttons(task)
    bot.send_message(chat['id'], "/t{task_id}".format(task_id=task_id), reply_markup=buttons, parse_mode='HTML')


def  update_task_message_text(task,message,user):
    header = escape_html(task.description)
    header += '\n\n'
    header += task_summary(task, user)

    if task.task_state==TASK_STATE_TODO:
        buttons = task_bottom_buttons(task,task_id = None)
    else:
        buttons = task_state_keyboard(task, row_width=4)

    bot.edit_message_text(text=header, chat_id = chat['id'], message_id = message, parse_mode='HTML', reply_markup=buttons )

#########################
# Buttons and Keyboards #
#########################
def task_bottom_buttons(task=None, task_id=None):
    task_id = task_id or task.id
    buttons = task_state_keyboard(task=task, task_id=task_id)
    buttons.row_width = 2
    buttons.add(
        button_update_assigned_to(task_id),
        button_attach_messages(task_id),
    )
    buttons.row_width = 1
    buttons.add(
        button_my_tasks()
    )
    return buttons


def task_state_keyboard(task, task_id=None, row_width=4):
    task_state = task and task.task_state or TASK_STATE_TODO
    task_id = task_id or task.id
    buttons = InlineKeyboardMarkup(row_width=row_width)
    buttons.add(
        *[InlineKeyboardButton(
            mark_state(TASK_STATE_TO_HTML[ts], ts, task_state),
            callback_data=encode_callback(ACTION_UPDATE_TASK_STATE, task_state=ts, task_id=task_id)
        ) for ts in [
            TASK_STATE_TODO,
            TASK_STATE_DONE,
            TASK_STATE_RELOCATED,
            TASK_STATE_CANCELED,
        ]
        ])
    return buttons


def assign_keyboard():
    reply_markup = ReplyKeyboardMarkup(row_width=4)
    reply_markup.add(
        *[KeyboardButton(
            '%s u%s' % (user_name, user_id)
        ) for user_id, user_name in USERS.items()]
    )
    return reply_markup


def button_update_assigned_to(task_id):
    return InlineKeyboardButton(
        '{emoji} Set Performer {emoji}'.format(emoji=EMOJI_UPDATE_ASSIGNED_TO),
        callback_data=encode_callback(ACTION_UPDATE_ASSIGNED_TO, task_id=task_id)
    )


def button_update_description(task_id):
    return InlineKeyboardButton(
        '{emoji} Update Description {emoji}'.format(emoji=EMOJI_UPDATE_DESCRIPTION),
        callback_data=encode_callback(ACTION_UPDATE_DESCRIPTION, task_id=task_id)
    )


def button_attach_messages(task_id):
    return InlineKeyboardButton(
        '{emoji} Attach Messages {emoji}'.format(emoji=EMOJI_ATTACH_MESSAGES),
        callback_data=encode_callback(ACTION_ATTACH_MESSAGES, task_id=task_id)
    )


def button_my_tasks():
    return InlineKeyboardButton(
        '{emoji} My Tasks {emoji}'.format(emoji=EMOJI_MY_TASKS),
        callback_data=encode_callback(ACTION_MY_TASKS)
    )


def button_tasks_from_me():
    return InlineKeyboardButton(
        '{emoji} Tasks From Me {emoji}'.format(emoji=EMOJI_TASKS_FROM_ME),
        callback_data=encode_callback(ACTION_TASKS_FROM_ME)
    )


def button_stop_attaching():
    return InlineKeyboardButton(
        '{emoji} Stop Attaching {emoji}'.format(emoji=EMOJI_STOP_ATTACHING),
        callback_data=encode_callback(ACTION_STOP)
    )


def button_cancel():
    return InlineKeyboardButton(
        '{emoji} Cancel {emoji}'.format(emoji=EMOJI_CANCEL_ACTION),
        callback_data=encode_callback(ACTION_CANCEL)
    )


def button_task(task, user_id, html=True):
    return InlineKeyboardButton(
        task_summary(task, user_id, html),
        callback_data=encode_callback(ACTION_TASK, task.id),
    )


###############################
# CONSTS and global variables #
###############################
NOT_FOUND_MESSAGE = "<i>Task doesn't exist or you don't have access to it</i>"

FROM_INDEX = 'from_id-task_state-index'
TO_INDEX = 'to_id-task_state-index'

# READ environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
USERS = os.environ.get('USERS', '{}')
if USERS:
    USERS = dict(json.loads(USERS))
DYNAMODB_TABLE_TASK = os.environ.get('DYNAMODB_TABLE_TASK')
DYNAMODB_TABLE_USER = os.environ.get('DYNAMODB_TABLE_USER')
LOG_LEVEL = os.environ.get('LOG_LEVEL')
MIN_UPDATE_ID = int(os.environ.get('MIN_UPDATE_ID', 0))
FORWARDING_DELAY = int(os.environ.get('FORWARDING_DELAY', 3))
REMINDER_DAYS = int(os.environ.get('REMINDER_DAYS', 14))

logger = logging.getLogger()
if LOG_LEVEL:
    logger.setLevel(getattr(logging, LOG_LEVEL))


dynamodb = boto3.client('dynamodb')
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
update = None
message = None
chat = None
user = None

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
TASK_STATE_RELOCATED = 1
TASK_STATE_DONE = 2
TASK_STATE_CANCELED = 3

# To get emoji code use
# http://www.webpagefx.com/tools/emoji-cheat-sheet/
# and https://pypi.python.org/pypi/emoji
EMOJI_TODO = u'\U0001f4dd'  # emoji.emojize(':memo:', use_aliases=True)
EMOJI_RELOCATED = u'\U0001f618'  # kissing
EMOJI_DONE = u'\u2705'  # emoji.emojize(':white_check_mark:', use_aliases=True)
EMOJI_CANCELED = u'\u274c'  # emoji.emojize(':x:', use_aliases=True)
EMOJI_TASK_TO = u'\u27a1'  # emoji.emojize(':arrow_right:', use_aliases=True)
EMOJI_TASK_FROM = u'\u2709'  # emoji.emojize(':envelope:', use_aliases=True)
EMOJI_AUTO_ATTACHED_MESSAGE = u'\U0001f9e9'  # Puzzle. No emoji alias. I got it from message's text
EMOJI_ATTACH_MESSAGES = EMOJI_AUTO_ATTACHED_MESSAGE
EMOJI_ATTACHED_MESSAGE = EMOJI_AUTO_ATTACHED_MESSAGE
EMOJI_ATTACHING_STOPPED = u'\U0001f44d'  # Thumbup
EMOJI_SEND_MESSAGE_TO_ATTACH = u'\u2709' + EMOJI_AUTO_ATTACHED_MESSAGE  # envelope + puzzle
EMOJI_NEW_TASK = u'\U0001f609'  # emoji.emojize(':wink:', use_aliases=True)
EMOJI_SEPARATOR_MY_TASKS = u'\U0001f68b' * 10  # emoji.emojize(':train:', use_aliases=True)
EMOJI_UPDATE_DESCRIPTION = u'\U0001f4d6'  # emoji.emojize(u":book:", use_aliases=True)
EMOJI_UPDATE_ASSIGNED_TO = u'\U0001f920'  # emoji.emojize(u"\U0001f920", use_aliases=True)
EMOJI_MY_TASKS = u'\u2b50'  # emoji.emojize(u":star:", use_aliases=True)
EMOJI_TASKS_FROM_ME = EMOJI_TASK_TO
EMOJI_STOP_ATTACHING = u'\U0001f44c'  # emoji.emojize(u":ok_hand:", use_aliases=True)
EMOJI_CANCEL_ACTION = u'\u270b'  # emoji.emojize(u":raised_hand:", use_aliases=True)
EMOJI_TIME = u'\U0001f550'  # emoji.emojize(u":clock1:", use_aliases=True)
EMOJI_NEW_TASK_FROM_ANOTHER = u'\U0001f381'  # emoji.emojize(u":gift:", use_aliases=True)
EMOJI_NEW_STATE_FROM_ANOTHER = u'\U0001f60d'  # emoji.emojize(u":heart_eyes:", use_aliases=True)
EMOJI_NEW_DESCRIPTION_FROM_ANOTHER = u'\U0001f914'  # thinking


TASK_STATE_TO_HTML = {
    TASK_STATE_TODO: "%s To-Do" % EMOJI_TODO,
    TASK_STATE_RELOCATED: "%s Relocated" % EMOJI_RELOCATED,
    TASK_STATE_DONE: "%s Done" % EMOJI_DONE,
    TASK_STATE_CANCELED: "%s Canceled" % EMOJI_CANCELED,
}


#####################
# Telegram wrappers #
#####################
def send(reply_text, reply_markup=None, reply=True):
    logger.debug('Send message: %s', reply_text)
    try:
        bot.send_message(chat['id'], reply_text, reply_to_message_id=reply and message['message_id'], parse_mode='HTML', reply_markup=reply_markup)
    except ApiException as e:
        res = e.result.json()
        if reply and res['description'] == "Bad Request: reply message not found":
            return send(reply_text, reply_markup=reply_markup, reply=False)

def notify_another_user(task, reply_text):
    another_user_id = None
    if user['id'] != task.from_id:
        another_user_id = task.from_id
    elif user['id'] != task.to_id:
        another_user_id = task.to_id

    if not another_user_id:
        return

    another_user_activity = User.load_by_id(another_user_id)
    if not another_user_activity.chat_id:
        return

    buttons = InlineKeyboardMarkup(row_width=1)
    buttons.add(button_task(task, another_user_id, html=False))
    buttons.add(button_tasks_from_me())
    buttons.add(button_my_tasks())

    bot.send_message(
        another_user_activity.chat_id,
        reply_text,
        parse_mode='HTML',
        reply_markup=buttons
    )


def escape_html(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


################
# Text Helpers #
################
def mark_state(state_html, state, current_state):
    if state != current_state:
        return state_html
    return '*' + state_html.upper()


def get_command_and_text(text):
    """split message into command and main text"""
    m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
    if m:
        # group(3) is a bot name
        return m.group(1), m.group(3)
    else:
        return None, text


def user2link(user):
    user_id = user['id']
    if str(user_id) in USERS:
        name = user_id2name(user_id)
    else:
        name = user2name(user)
    user_link = '<a href="tg://user?id=%s">%s</a>' % (user_id, name)
    return user_link


def user2name(user):
    name = user.get('first_name')
    if user.get('last_name'):
        name += ' %s' % user.get('last_name')

    if user.get('username'):
        name += ' (@%s)' % (user.get('username'))

    return name


def user_id2name(user_id):
    # USERS' keys are strings because it's json
    return USERS.get(str(user_id)) or 'User%s' % user_id


def message2description(message):
    description = None
    if message.get('text'):
        description = message.get('text')
    else:
        for key, text in MEDIA2DESCRIPTION:
            if message.get(key):
                description = text
                break
    if not description:
        description = '<i>Task</i>'
    if str(user['id']) not in USERS:
        user_link = user2link(user)
        description = '%s\nby %s' % (description, user_link)
    return description


def task_summary(task, user_id, html=True):
    def wrap(text):
        if not html:
            return text
        return '<i>%s</i>' % text

    state = '%s' % TASK_STATE_TO_HTML[task.task_state]
    state = wrap(state)
    another_user = ''
    if user_id != task.from_id:
        another_user = '%s %s' % (EMOJI_TASK_FROM, user_id2name(task.from_id))
    elif user_id != task.to_id:
        another_user = '%s %s' % (EMOJI_TASK_TO, user_id2name(task.to_id))
    if another_user:
        another_user = wrap(another_user)
    time = '%s %s' % (EMOJI_TIME, pretty_date(task.telegram_unixtime)) if task.telegram_unixtime else ''
    time = wrap(time)
    task_command = '/t%s' % task.id
    msg_num = ''
    if task.msg_num and task.msg_num > 1:
        msg_num = "\n%s attached messages" % task.msg_num
        msg_num = wrap(msg_num)

    summary = ' '.join([t for t in [
        state,
        time,
        another_user,
        task_command,
        msg_num,
    ] if t])
    return summary


# from https://stackoverflow.com/questions/1551382/user-friendly-time-format-in-python
def pretty_date(time=False):
    """
    Get a datetime object or a int() Epoch timestamp and return a
    pretty string like 'an hour ago', 'Yesterday', '3 months ago',
    'just now', etc
    """
    now = datetime.now()
    if type(time) is int:
        diff = now - datetime.fromtimestamp(time)
    elif isinstance(time, datetime):
        diff = now - time
    elif not time:
        diff = now - now
    second_diff = diff.seconds
    day_diff = diff.days

    if day_diff < 0:
        return ''

    if day_diff == 0:
        if second_diff < 10:
            return "just now"
        if second_diff < 60:
            return str(second_diff) + " seconds ago"
        if second_diff < 120:
            return "a minute ago"
        if second_diff < 3600:
            return str(second_diff / 60) + " minutes ago"
        if second_diff < 7200:
            return "an hour ago"
        if second_diff < 86400:
            return str(second_diff / 3600) + " hours ago"
    if day_diff == 1:
        return "Yesterday"
    if day_diff < 7:
        return str(day_diff) + " days ago"
    if day_diff < 31:
        return str(day_diff / 7) + " weeks ago"
    if day_diff < 365:
        return str(day_diff / 30) + " months ago"
    return str(day_diff / 365) + " years ago"


#############
# Callbacks #
#############
ACTION_UPDATE_TASK_STATE = 'us'

ACTION_UPDATE_DESCRIPTION = 'ud'
ACTION_UPDATE_ASSIGNED_TO = 'ua'
ACTION_ATTACH_MESSAGES = 'am'
ACTION_TASK = 'at'

ACTION_MY_TASKS = 'mt'
ACTION_TASKS_FROM_ME = 'tfm'
ACTION_STOP = 's'
ACTION_CANCEL = 'c'

ACTIONS_WITHOUT_DATA = [ACTION_MY_TASKS, ACTION_STOP, ACTION_CANCEL, ACTION_TASKS_FROM_ME]
# ACTIONS_WITH_TASK = [ACTION_UPDATE_DESCRIPTION, ACTION_UPDATE_ASSIGNED_TO, ACTION_ATTACH_MESSAGES]


def encode_callback(action, task_id=None, task_state=None):
    if action == ACTION_UPDATE_TASK_STATE:
        return '%s_%s_%s' % (
            action,
            task_id,
            task_state,
        )
    elif action in ACTIONS_WITHOUT_DATA:
        return action
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
    if action in ACTIONS_WITHOUT_DATA:
        return result

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
    def load_by_id(cls, id, raise_if_not_found=False):
        res = dynamodb.get_item(
            TableName=cls.TABLE,
            Key={
                cls.PARTITION_KEY: cls.elem_to_num(id),
            }
        )
        if not res.get('Item'):
            if raise_if_not_found:
                raise Exception("Attempt for loading unexisting record: %s")
            else:
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
                self.PARTITION_KEY: DynamodbItem.elem_to_num(getattr(self, self.PARTITION_KEY))
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


# DYNAMODB_TABLE_USER
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

    def update_activity_task_time(self):
        return self.update('activity', 'task_id', 'telegram_unixtime')

    def update_time(self):
        return self.update('telegram_unixtime')

    # Reading
    @classmethod
    def load_by_id(cls, id, chat=None):
        res = super(User, cls).load_by_id(id)
        if chat:
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
#   "task_state": STATE, // STATE: O=TODO, 1=RELOCATED, 2=DONE, 3=CANCELED
#
#   // Projected keys
#   "description": "Short representation of the TODO",
#   "telegram_unixtime": CREATION_TIMESTAMP,
#
#   // Normal keys
#   "msg_num": INTEGER,
#   "next_reminder": UNIXTIME,
#   "messages": [CHAT_ID + '_' + MESSAGE_ID],
# }


class Task(DynamodbItem):
    STR_PARAMS = ['description']
    INT_PARAMS = ['id', 'from_id', 'to_id', 'task_state', 'telegram_unixtime', 'msg_num', 'next_reminder']
    CHAT_MSG_PARAMS = ['messages']
    TABLE = DYNAMODB_TABLE_TASK

    def __init__(self, id=0, task_state=TASK_STATE_TODO, user_id=0):
        self.id = id
        self.task_state = task_state
        self.from_id = user_id
        self.to_id = user_id
        self.telegram_unixtime = 0
        self.msg_num = 0
        self.description = '*New Task*'
        self.messages = []

    # Preparing
    @staticmethod
    def _message2tuple(message):
        chat = message.get('chat')
        return (chat['id'], message['message_id'])

    def add_message(self, message):
        self.messages.append(self._message2tuple(message))
        self.msg_num += 1

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
        filter_expression = None
        if to_me:
            condition = "to_id = :user_id"
            index = TO_INDEX
        else:
            condition = "from_id = :user_id"
            filter_expression = "to_id <> :user_id"
            index = FROM_INDEX

        if task_state is None:
            pass
        else:
            condition += " and task_state = :task_state"
            args[':task_state'] = Task.elem_to_num(task_state)

        query_kwargs = dict(
            TableName=cls.TABLE,
            IndexName=index,
            Select='ALL_PROJECTED_ATTRIBUTES',
            KeyConditionExpression=condition,
            ExpressionAttributeValues=args,
        )
        if filter_expression:
            query_kwargs['FilterExpression'] = filter_expression

        result = dynamodb.query(**query_kwargs)

        return (cls.load_from_dict(task_dict) for task_dict in result['Items'])

    @classmethod
    def get_tasks_to_remind(cls, user_id, unixtime_now):
        # Doc: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.query
        condition = "to_id = :user_id and task_state = :task_state"
        filter_expression = "attribute_not_exists(next_reminder) or next_reminder < :unixtime_now"
        args = {
            ':user_id': Task.elem_to_num(user_id),
            ':task_state': Task.elem_to_num(TASK_STATE_TODO),
            ':unixtime_now': Task.elem_to_num(unixtime_now),
        }
        query_kwargs = dict(
            TableName=cls.TABLE,
            IndexName=TO_INDEX,
            Select='ALL_PROJECTED_ATTRIBUTES',
            KeyConditionExpression=condition,
            ExpressionAttributeValues=args,
            FilterExpression=filter_expression
        )

        result = dynamodb.query(**query_kwargs)

        return (cls.load_from_dict(task_dict) for task_dict in result['Items'])

    # Writing
    @classmethod
    def _dump_messages(self, array):
        return self.elem_to_array_of_str(
            ['%s_%s' % (m[0], m[1]) for m in array]
        )

    def to_dict(self):
        res = super(Task, self).to_dict()
        for ss_param in self.CHAT_MSG_PARAMS:
            res[ss_param] = self._dump_messages(getattr(self, ss_param))
        return res

    def update_task_state(self):
        return self.update('task_state')

    def update_description(self):
        return self.update('description')

    def update_assigned_to(self):
        return self.update('to_id')

    def update_next_reminder(self):
        return self.update('next_reminder')

    def add_and_update_messages(self, message):
        array = [self._message2tuple(message)]
        AttributeUpdates = {}
        AttributeUpdates['messages'] = {
            'Value': self._dump_messages(array),
            'Action': 'ADD',
        }
        AttributeUpdates['msg_num'] = {
            'Value': self.elem_to_num(1),
            'Action': 'ADD',
        }
        return self._update(AttributeUpdates)
# EOF
