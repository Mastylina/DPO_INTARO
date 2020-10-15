import datetime
from main import db, session, Base
from sqlalchemy.orm import relationship


class User(Base):
    __tablename__ = 'user'
    id = db.Column('user_id', db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), nullable=False, unique=True)
    balance = db.Column(db.Numeric(10, 2), default=0.00)
    transaction = relationship("Transaction", backref='users')


class Transaction(Base):
    __tablename__ = 'transaction'
    id = db.Column('transaction_id', db.Integer, primary_key=True)
    user_id = db.Column(db.Integer(), db.ForeignKey(User.id))
    transaction_time = db.Column(db.DateTime(),
                                 default=datetime.datetime.now,
                                 nullable=False)
    balance_change = db.Column(db.Numeric(10, 2), nullable=False)
    current_balance = db.Column(db.Numeric(10, 2), nullable=False)
