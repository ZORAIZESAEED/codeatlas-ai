from app.auth.security import hash_password, verify_password
from app.db.models import User


class InvalidCredentials(Exception):
    """Raised when a username/password pair does not match a stored account."""


class AuthService:
    """Looks up accounts and checks credentials during sign-in and registration."""

    def __init__(self, session):
        self.session = session

    def authenticate(self, username: str, password: str) -> User:
        user = self.session.query(User).filter_by(username=username).first()
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials(username)
        if not user.is_active:
            raise InvalidCredentials(username)
        return user

    def register(self, username: str, email: str, password: str) -> User:
        if self.session.query(User).filter_by(username=username).first():
            raise ValueError("username already taken")
        user = User(username=username, email=email, password_hash=hash_password(password))
        self.session.add(user)
        self.session.commit()
        return user
