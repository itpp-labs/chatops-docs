# Copyright 2019 Ivan Yelizariev <https://it-projects.info/team/yelizariev>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
import sys
import os
import logging
import re


logger = logging.getLogger()

# Set Debug level as environment variable (DEBUG, WARNING, ERROR, CRITICAL, INFO), by default: INFO
logger.setLevel(getattr(logging,os.environ.get('LOGGING_LEVEL','INFO')))

RESPONSE_200 = {
    "statusCode": 200,
    "headers": { },
    "body": ""
}
MEDIA = {'sticker': 'send_sticker', 'voice': 'send_voice', 'video': 'send_video', 'document': 'send_document', 'video_note': 'send_video_note'}

SYSTEM_EMPTY_MESSAGES = [
    'left_chat_member',
    'new_chat_members',
    'new_chat_photo',
    'pinned_message',
    'new_chat_title',
    'delete_chat_photo']

# Function, that returns a string with html-markups according to entities in message
def get_formatted_text(text,entys):
        i_uf_st = 0 #notformatted start index
        i_uf_end =0 #nonformatted end index
        f_text = ""
        for ent in entys:
            i_f_st =  ent['offset'] #formatted start index
            i_f_end = ent['offset']+ent['length'] #formatted end index
            i_uf_end = i_f_st

            if i_uf_st != i_uf_end:
                f_text += "%s" % (text[i_uf_st:i_uf_end])

            i_uf_st = i_f_end

            if ent['type'] == "bold":
                f_text += "<b>%s</b>" % (text[i_f_st:i_f_end])
            elif ent['type'] == "italic":
                f_text += "<i>%s</i>" % (text[i_f_st:i_f_end])
            elif ent['type'] == "code":
                f_text += "<code>%s</code>" % (text[i_f_st:i_f_end])
            else:
                f_text += "%s" % (text[i_f_st:i_f_end])

        if i_uf_st != len(text):
            f_text += "%s" % (text[i_uf_st:])

        return f_text

def lambda_handler(event, context):
    logger.debug("Event: \n%s", event)
    logger.debug("Context: \n%s", context)
    # READ webhook data

    # Object Update in json format.
    # See https://core.telegram.org/bots/api#update
    update = telebot.types.JsonDeserializable.check_json(event["body"])

    # READ environment variables
    BOT_TOKEN = os.environ['BOT_TOKEN']
    TARGET_GROUP = int(os.environ.get('TARGET_GROUP', 0))
    ANONYMOUS_REPLY = os.environ.get('ANONYMOUS_REPLY') != 'False'
    ANONYMOUS_REQUEST_FROM_GROUPS = os.environ.get('ANONYMOUS_REQUEST_FROM_GROUPS') != 'False'

    ACCESS_BOT_LIST = [int(id.strip(' ')) for id in os.environ['ACCESS_BOT_LIST'].split(',')] if os.environ.get('ACCESS_BOT_LIST')  else None

    # PARSE
    message = update.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    # Only work with disabled threaded mode. See https://github.com/eternnoir/pyTelegramBotAPI/issues/161#issuecomment-343873014
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

    # Handle if user has an access to bot
    if ACCESS_BOT_LIST is not None:
        if user['id'] not in ACCESS_BOT_LIST:
            bot.send_message(chat['id'], '<i>This is the private bot.\n</i>'
            '<i>The good news is that you can deploy a similar bot for yourself:\n</i>'
                r'https://chatops.readthedocs.io/en/latest/todo-bot/index.html',reply_to_message_id=message['message_id'], parse_mode='HTML')
            return RESPONSE_200

    def get_command_and_text(text):
        """split message into command and main text"""
        m = re.match('(/[^ @]*)([^ ]*)(.*)', text, re.DOTALL)
        if m:
            # group(3) is a bot name
            return m.group(1), m.group(3)
        else:
            return None, text

    command, main_text = get_command_and_text(message.get('text', ''))

    if command and command == '/thischat':
        bot.send_message(chat['id'], chat['id'], reply_to_message_id=message['message_id'])
        return RESPONSE_200

    if command and command == '/myid':
        bot.send_message(chat['id'], user['id'], reply_to_message_id=message['message_id'])
        return RESPONSE_200

    original_chat = None
    original_message_id = None

    # Handling reply to bot_request

    if not command and message.get('reply_to_message'):
        # try to get the message reference via reply_to_message
        parent_text = message['reply_to_message'].get('text', '')
        m = re.search('msg:([0-9-]*):?([0-9-]*)$', parent_text)
        if m:
            original_message_id = m.group(1)
            original_chat = m.group(2)

    # REPLY
    if not main_text and not any(message.get(key) for key in MEDIA) and not message.get('photo'):

        # Handling system 'Empty'-messages
        for msg in SYSTEM_EMPTY_MESSAGES:
            if msg in message:
                return RESPONSE_200

        bot.send_message(chat['id'], "<i>Empty message is ignored</i>", reply_to_message_id=message['message_id'], parse_mode='HTML')
        return RESPONSE_200

    is_from_target_group = chat['id'] == TARGET_GROUP
    if is_from_target_group and not original_chat:
        bot.send_message(chat['id'], "<i>At this chat you can only reply to the requests</i>", reply_to_message_id=message['message_id'], parse_mode='HTML')
        return RESPONSE_200

    reply_chat = original_chat or TARGET_GROUP

    from_group = not is_from_target_group and chat.get('title')
    show_username = not (is_from_target_group and ANONYMOUS_REPLY or from_group and ANONYMOUS_REQUEST_FROM_GROUPS)

    if from_group:
        from_group = '<em>from</em> <b>%s</b>' % from_group

    # If there is entities in message

    if 'entities' in message is not None:
        main_text = get_formatted_text(main_text,message['entities'])

    if not main_text:
        main_text = message.get('caption')

    reply_text = "%s%s\n%s\n<i>msg:%s%s</i>" % (
        from_group or '',
        ': ' if main_text and (from_group or show_username) else '',
        main_text,
        message['message_id'],
        ':%s' % chat['id'] if not is_from_target_group else '')

    if show_username:
        author_link = "<a href=\"tg://user?id=%s\">%s</a>" % (user['id'], user['first_name'])
        reply_text = "%s%s%s" % (
            author_link,
            ' ' if from_group else '',
            reply_text)

    for key, method in MEDIA.items():
        if message.get(key):
            # media are sent separately
            getattr(bot, method)(reply_chat, message[key]['file_id'], reply_to_message_id=original_message_id or None)

    if message.get('photo'):
        photo = message.get('photo')[-1]
        bot.send_photo(reply_chat, photo['file_id'], caption=message.get('caption'))

    bot.send_message(reply_chat, reply_text, reply_to_message_id=original_message_id or None, parse_mode='HTML')

    return RESPONSE_200
