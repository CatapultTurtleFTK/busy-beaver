import logging
from urllib.parse import urlencode
import uuid

from flask import jsonify, request
from flask.views import MethodView

from busy_beaver import slack
from busy_beaver.extensions import db
from busy_beaver.config import GITHUB_CLIENT_ID, GITHUB_REDIRECT_URI
from busy_beaver.models import User

logger = logging.getLogger(__name__)

SEND_LINK_COMMANDS = ["connect"]
RESEND_LINK_COMMANDS = ["reconnect"]
ALL_LINK_COMMANDS = SEND_LINK_COMMANDS + RESEND_LINK_COMMANDS

UNKNOWN_COMMAND = (
    "I don't recognize your command. Type `connect` to link your GitHub account."
)
ACCOUNT_ALREADY_ASSOCIATED = (
    "You have already associated a GitHub account with your Slack handle. "
    "Please type `reconnect` to link to a different account."
)
VERIFY_ACCOUNT = (
    "Follow the link below to validate your GitHub account. "
    "I'll reference your GitHub username to track your public activity."
)


class SlackEventSubscriptionResource(MethodView):
    """Callback endpoint for Slack event subscriptions

    TODO: refactor to make more modular and readable, bot makes minimal use of slack
    event subscriptions; low priority
    """

    def post(self):
        data = request.json
        logger.info("[Busy-Beaver] Recieved event from Slack", extra={"req_json": data})

        verification_request = data["type"] == "url_verification"
        if verification_request:
            logger.info("[Busy-Beaver] Slack -- API Verification")
            return jsonify({"challenge": data["challenge"]})

        # bot can see its own DMs
        event = data["event"]
        msg_from_bot = event.get("subtype") == "bot_message"
        if event["type"] == "message" and msg_from_bot:
            return jsonify(None)

        dm_to_bot = event["channel_type"] == "im"
        if event["type"] == "message" and dm_to_bot:
            reply_to_user_with_github_login_link(event)
        return jsonify(None)


def reply_to_user_with_github_login_link(message):
    """Todo break this up... Research spike to find a good abstraction"""

    chat_text = str.lower(message["text"])
    slack_id = message["user"]
    channel_id = message["channel"]

    if chat_text not in ALL_LINK_COMMANDS:
        logger.info("[Busy-Beaver] Unknown command")
        slack.post_message(UNKNOWN_COMMAND, channel_id=channel_id)
        return

    user_record = User.query.filter_by(slack_id=slack_id).first()
    if user_record and chat_text in SEND_LINK_COMMANDS:
        logger.info("[Busy-Beaver] Slack acount already linked to GitHub")
        slack.post_message(ACCOUNT_ALREADY_ASSOCIATED, channel_id=channel_id)
        return

    # generate unique identifer to track user during authentication process
    state = str(uuid.uuid4())
    if user_record and chat_text in RESEND_LINK_COMMANDS:
        logger.info("[Busy-Beaver] Relinking GitHub account.")
        user_record.github_state = state
        db.session.add(user_record)
        db.session.commit()
    if not user_record:
        logger.info("[Busy-Beaver] New user. Linking GitHub account.")
        user = User()
        user.slack_id = slack_id
        user.github_state = state
        db.session.add(user)
        db.session.commit()

    data = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "state": state,
    }
    query_params = urlencode(data)
    url = f"https://github.com/login/oauth/authorize?{query_params}"
    attachment = [
        {
            "fallback": url,
            "attachment_type": "default",
            "actions": [
                {"text": "Associate GitHub Profile", "type": "button", "url": url}
            ],
        }
    ]
    slack.post_message(VERIFY_ACCOUNT, channel_id=channel_id, attachments=attachment)
