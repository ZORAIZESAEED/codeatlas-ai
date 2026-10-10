import smtplib
from email.message import EmailMessage

from app.config import get_settings

RESET_TEMPLATE = """Hello {username},

Someone asked to reset the password for your account.
Use this link within 30 minutes: {link}
"""


def send_password_reset_email(username: str, address: str, link: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = "Reset your password"
    message["To"] = address
    message.set_content(RESET_TEMPLATE.format(username=username, link=link))
    with smtplib.SMTP(settings.smtp_host) as smtp:
        smtp.send_message(message)
