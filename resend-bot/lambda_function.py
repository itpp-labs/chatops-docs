import telebot  # https://github.com/eternnoir/pyTelegramBotAPI
import sys
import os
import logging
import re


logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.INFO)

RESPONSE_200 = {
    "statusCode": 200,
    "headers": { },
    "body": ""
}
MEDIA = {'sticker': 'send_sticker', 'voice': 'send_voice', 'video': 'send_video', 'document': 'send_document', 'video_note': 'send_video_note'}


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

    # PARSE
    message = update.get('message')
    if not message:
        return RESPONSE_200

    chat = message.get('chat')
    user = message.get('from')
    # Only work with disabled threaded mode. See https://github.com/eternnoir/pyTelegramBotAPI/issues/161#issuecomment-343873014
    bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

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

    original_chat = None
    original_message_id = None

    if not command and message.get('reply_to_message'):
        # try to get the message reference via reply_to_message
        parent_text = message['reply_to_message'].get('text', '')
        m = re.search('msg:([0-9-]*):?([0-9-]*)$', parent_text)
        if m:
            original_message_id = m.group(1)
            original_chat = m.group(2)

    # REPLY
    if not main_text and not any(message.get(key) for key in MEDIA) and not message.get('photo'):
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