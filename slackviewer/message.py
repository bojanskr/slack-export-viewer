from __future__ import unicode_literals

import datetime
import logging
import emoji


class Message(object):

    _DEFAULT_USER_ICON_SIZE = 72

    def __init__(self, formatter, message, channel_id, slack_name):
        self._formatter = formatter
        self._message = message
        # default is False, we update it later if its a thread message
        self.is_thread_msg = False
        # used only with --since flag. Default to True, will update in the function
        self.is_recent_msg = True
        # Channel id is not part of self._message - at least not with slackdump
        self.channel_id = channel_id
        # slack name that is in the url https://<slackname>.slack.com
        self.slack_name = slack_name

    def __repr__(self):
        message = self._message.get("text")
        if message and len(message) > 20:
            message = message[:20] + "..."

        return f"<Message({self.username}@{self.time}: {message})>"

    ##############
    # Properties #
    ##############

    @property
    def user_id(self):
        if "user" in self._message:
            return self._message["user"]
        elif "bot_id" in self._message:
            return self._message["bot_id"]
        else:
            logging.error("No user ID on %s", self._message)


    @property
    def user(self):
        return self._formatter.find_user(self._message)

    @property
    def username(self):
        try:
            return self.user.display_name
        except KeyError:
            # In case this is a bot or something, we fallback to "username"
            if "username" in self._message:
                return self._message["username"]
            elif "user" in self._message:
                return self.user_id
            elif "bot_id" in self._message:
                return self._message["bot_id"]
            else:
                return None

    @property
    def time(self):
        # Check if 'ts' key exists in the dictionary
        if "ts" in self._message:
            # Handle this: "ts": "1456427378.000002"
            tsepoch = float(self._message["ts"].split(".")[0])
            return str(datetime.datetime.fromtimestamp(tsepoch)).split('.')[0]
        else:
            return None  # or return a suitable default value

    @property
    def attachments(self):
        return [ LinkAttachment("ATTACHMENT", entry, self._formatter)
            for entry in self._message.get("attachments", []) ]

    @property
    def files(self):
        if "file" in self._message: # this is probably an outdated case
            allfiles = [self._message["file"]]
        else:
            allfiles = self._message.get("files", [])
        return [ LinkAttachment("FILE", entry, self._formatter) for entry in allfiles ]

    @property
    def msg(self):
        text = self._message.get("text")
        if text:
            # There is a case where the message["text"] is much shorter as the
            # actual message. It is unclear when or why.
            #
            # It might be around block['type'] == 'header', which is the trigger
            # here. But it might also be applicable to other cases or depending
            # on how an API call is done. All observed messages here have been
            # done through the Slack API.
            #
            # Technically blocks are what Slack recommends to use, while the
            # 'text' field is the fall back. 'text' field also seems to be used
            # for notifications text
            use_blocks = False
            if "blocks" in self._message and self._message["blocks"]:
                for block in self._message["blocks"]:
                    if block["type"] == "header":
                        use_blocks = True
                        break
            if use_blocks:
                text = self._generate_blocks_text(self._message["blocks"])

            text = self._formatter.render_text(text)
        return text

    def _generate_blocks_text(self, blocks):
        """Build a message together from various message["blocks"]"""
        text = ""
        for block in blocks:
            if "text" in block:
                text += self._format_block_type(block['text'], block["type"])

            elif "fields" in block:
                for field in block["fields"]:
                    text += self._format_block_type(field, block["type"])

            elif "elements" in block:
                for element in block["elements"]:
                    text += self._format_block_type(element, block["type"])

            elif "type" in block and block["type"] == "divider":
                text += "---\n"

            else:
                logging.warning(f"Unknown block type: {block}")

        return text

    def _format_block_type(self, text_obj, b_type):
        """Format the text based on the block type"""
        if "text" not in text_obj:
            logging.warning(f"Block Type {b_type}: Missing 'text' in {text_obj}")
            return "unsupported_block({b_type}: {text_obj})\n\n"

        text = text_obj["text"]

        if "type" in text_obj and text_obj["type"] not in ["plain_text", "mrkdwn", "button"]:
            logging.warning(f"Block Type {b_type}: Unsupported text type '{text_obj['type']}' for {text_obj}")
            return f"unsupported_block({b_type}: {text})\n\n"

        if "type" in text_obj and text_obj["type"] == "button":
            if "text" in text_obj['text']:
                text = f"Slack_Button({text['text']})"

        if b_type == "header":
            return f"*{text}*\n\n"
        elif b_type == "section":
            return f"{text}\n\n"
        elif b_type == "context":
            return f"<small>{text}</small>\n"
        elif b_type == "actions":
            return f"Slack_Action({text})\n"
        else:
            logging.warning(f"Unsupported block type '{b_type}' for {text_obj}")
            return f"unsupported_block({b_type}: {text_obj}\n\n)"

    def user_message(self, user_id):
        return {"user": user_id}

    def usernames(self, reaction):
        return [
            self._formatter.find_user(self.user_message(user_id)).display_name
            for user_id
            in reaction.get("users")
            if self._formatter.find_user(self.user_message(user_id))
        ]

    @property
    def reactions(self):
        reactions = self._message.get("reactions", [])
        return [
            {
                "usernames": self.usernames(reaction),
                "name": emoji.emojize(
                    self._formatter.slack_to_accepted_emoji(':{}:'.format(reaction.get("name"))),
                    language='alias'
                )
            }
            for reaction in reactions
        ]

    @property
    def img(self):
        try:
            return self.user.image_url(self._DEFAULT_USER_ICON_SIZE)
        except KeyError:
            return ""

    @property
    def id(self):
        return self.time

    @property
    def subtype(self):
        return self._message.get("subtype")

    @property
    def permalink(self):
        permalink = f"https://{self.slack_name}.slack.com/archives/{self.channel_id}/p{self._message['ts'].replace('.','')}"
        if "thread_ts" in self._message:
            permalink += f"?thread_ts={self._message['thread_ts']}&cid={self.channel_id}"
        return permalink


class LinkAttachment():
    """
    Wrapper class for entries in either the "files" or "attachments" arrays.
    """

    _DEFAULT_THUMBNAIL_SIZE = 360

    # Fields that need to be processed for markup (and possibly markdown)
    _TEXT_FIELDS = {"pretext", "text", "footer"}

    def __init__(self, attachment_type, raw, formatter):
        self._type = attachment_type
        self._raw = raw
        self._formatter = formatter

    def __getitem__(self, key):
        content = self._raw[key]
        if content and key in self._TEXT_FIELDS:
            process_markdown = (key in self._raw.get("mrkdwn_in", []))
            content = self._formatter.render_text(content, process_markdown)
        return content

    def thumbnail(self, size=None):
        size = size if size else self._DEFAULT_THUMBNAIL_SIZE
        # ATTACHMENT type
        if "image_url" in self._raw:
            logging.debug("image_url path")
            return {
                "src": self._raw["image_url"],
                "width": self._raw.get("image_width"),
                "height": self._raw.get("image_height"),
            }
        else: # FILE type
            thumb_key = "thumb_{}".format(size)
            logging.debug("thumb path" + thumb_key)
            if thumb_key not in self._raw:
                # let's try some fallback logic
                thumb_key = "thumb_{}".format(self._raw.get("filetype"))
                if thumb_key not in self._raw:
                    # pick the first one that shows up in the iterator
                    candidates = [k for k in self._raw.keys()
                        if k.startswith("thumb_") and not k.endswith(("_w","_h"))]
                    if candidates:
                        thumb_key = candidates[0]
                        logging.info("Fell back to thumbnail key %s for [%s]",
                            thumb_key, self._raw.get("title"))
            if thumb_key in self._raw:
                return {
                    "src": self._raw[thumb_key],
                    "width": self._raw.get(thumb_key + "_w"),
                    "height": self._raw.get(thumb_key + "_h"),
                }
            else:
                logging.info("No thumbnail found for [%s]", self._raw.get("title"))

    @property
    def is_image(self):
        return self._raw.get("mimetype", "").startswith("image/")

    @property
    def link(self):
        if "from_url" in self._raw:
            return self._raw["from_url"]
        else:
            return self._raw.get("url_private")

    @property
    def fields(self):
        """
        Fetch the "fields" list, and process the text within each field, including markdown
        processing if the message indicates that the fields contain markdown.

        Only present on attachments, not files--this abstraction isn't 100% awesome.'
        """
        process_markdown = ("fields" in self._raw.get("mrkdwn_in", []))
        fields = self._raw.get("fields", [])
        if fields:
            logging.debug("Rendering with markdown markdown %s for %s", process_markdown, fields)
        return [
            {"title": e["title"], "short": e.get("short", False), "value": self._formatter.render_text(e["value"], process_markdown)}
            for e in fields
        ]
