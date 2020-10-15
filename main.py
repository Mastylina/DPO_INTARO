from flask import Flask, jsonify, request
from flask_apscheduler import APScheduler
import sqlalchemy as db
import retailcrm
import json
import time
from decimal import Decimal
from threading import Thread
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from config import Config

#создаем приложение фласка и передаем ему название нашего приложения
app = Flask(__name__)
#объект для вызова проверки прошел ли день с момента старта скрипта
scheduler = APScheduler()
#объект RetailCRM API для удобного взаимодействия с методами API
client = retailcrm.v5(Config.crm_url, Config.api_key)
#привязываем базу данных
#если не существует, то создаем
engine = create_engine(Config.db_url)
#сессия базы данных, как выполнили запрос, то закрываем соединение
#sessionmaker - настройка параметров для сессии
#autocommit - говорим, что сами будем подвтерждать изменения в бд
#autoflush - говорим, что сами будем отслеживать добавление в бд
#bind - указываем к какому именно объекту бд прикрепляем сессию
session = scoped_session(sessionmaker(autocommit=False,
                                      autoflush=False,
                                      bind=engine))

#базовый класс для создания собственных через ORM
Base = declarative_base()
Base.query = session.query_property()

from models import *

#в случае несуществования таблиц в бд или самой бд, создаем
Base.metadata.create_all(bind=engine)


#усправляющая функция для добавления бонусов
def scheduledTask():
    try:
        dictionary = {}
        limit = 100
        page = 1
        #расчитываем дату предыдущего дня и переводим в нужный формат
        paidAtFrom = (datetime.datetime.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        #получаем дату текущего дня
        paidAtTo = datetime.datetime.now().strftime("%Y-%m-%d")
        #фильтр поиска заказов
        #от предыдущего дня, до текущего
        #получение всех заказов
        filters = {"paidAtFrom": paidAtFrom,
                   "paidAtTo": paidAtTo}
        #вызываем метод получения заказов
        response = client.orders(filters, limit, page)
        #помещаем все заказы за день
        data = response._Response__response_body['orders']
        #проходимся по каждому из заказу
        for d in data:
            #помещаем в переменную сумму платежа
            total_sum = d['totalSumm']
            #помещаем в переменную номер телефона без "+" в начале
            phone_number = d['phone'][1:]
            #если заказчик с таким телефоном встречается впервые,то
            #по ключу в виде телефонного номера помещаем сумму заказа
            if phone_number not in dictionary.keys():
                dictionary[phone_number] = total_sum
            #если заказчик делает уже не первый заказ за день, то
            #плюсуем его стоимость заказа
            else:
                dictionary[phone_number] += total_sum
        
        #пробегаемся по каждому пользователю
        for key in dictionary:
            #начисляем 5% от потраченной суммы за день
            add_bonus(phone_number=key,
                      total_sum=dictionary[key])
    except Exception as e:
        raise e


#добавление бонусов 5% раз в день от общей суммы
def add_bonus(phone_number, total_sum):
    try:
        #находим пользователя с таким номером телефона
        user = User.query.filter(User.phone_number == phone_number).first()
        #расчитываем 5% от общей суммы заказов
        balance_change = Decimal(total_sum * 0.05)
        #если пользователь с таким телефоном найден, то
        if user:
            #добавляем 5%от суммы заказов за день к текущему балансу
            user.balance += balance_change
            #создаем новый транзакт с полученными данными
            new_transact = Transaction(user_id = user.id,
                                    balance_change = balance_change,
                                    current_balance = user.balance)

        #если такого пользователя нет в системе бонусов
        else:
            #создаем нового пользователя с таким телефоном
            new_user = User(phone_number)
            #добавляем вычисленную сумму к балансу пользователя
            new_user.balance += balance_change
            #добавляем данного пользователя в базу данных
            session.add(new_user)
            #создаем новый транзакт с полученными данными
            new_transact = Transaction(user_id = new_user.id,
                                    balance_change = balance_change,
                                    current_balance = new_user.balance)

        #добавляем новый транзакт в базу данных
        session.add(new_transact)
        #применяем изменения
        session.commit()
    except Exception as e:
        raise e


#пост метод для проверки работоспособности сервера
@app.route('/echo', methods=['POST'])
def echo_method():
    try:
        params = request.get_json()
        msg = {"msg": f"{params['msg']} hello by Server!"}
    except Exception as e:
        msg = {"msg": "Error! Send msg key!"}
    return msg


#пост метод для возврата кода успешности включения/отключения модуля
@app.route('/actvity', methods=['POST'])
def activity():
   return jsonify({"success": True})


#обновляем статус инвойса нового заказа
def update_invoice(invoiceUuid, paymentId):
    time.sleep(2)
    updateInvoice = {"invoiceUuid": invoiceUuid,
                    "paymentId": paymentId,
                    "status": "succeeded",
                    "refundable": False,
                    "cancellable": False}
    #вызываем метод обновления инвойса передав ему сформированный объект данных
    client.payment_update_invoice(update_invoice=updateInvoice)


#метод создания оплаты
@app.route('/payment/create', methods=['POST'])
def payment_create():
    try:
        #получаем переденныезначения
        params = request.form.get('create')
        #получаем объект create из переднных данных
        #и преобразуем его json
        data = json.loads(params)
        #из json объекта достаем сумму заказа
        amount = data['amount']
        try:
            #пытаемся получить номер телефона заказчика
            #он может быть не задан и выбросит исключение
            phone = data['customer']['phone']
        except Exception as e:
            #если номер телефона не задан, то выбрасываем ошибку
            return jsonify({"errorMsg": "Не указан телефон пользователя", 
                            "success": False})
        
        #получаем пользователя из нашей бд по этому номеру телефона
        selected_user = User.query.filter(User.phone_number == phone).first()
        #если пользователь в базе существует
        if selected_user:
            #получаем баланс пользователя
            user_balance = selected_user.balance
            #если баланса пользователя достаточно для оплаты, то идем дальше
            if user_balance >= amount:
                #у данного баланса пользователя отнимаем сумму заказа
                selected_user.balance -= amount
                #создаем новый транзакт для отслеживания изменения баланса
                new_transact = Transaction(user_id = selected_user.id,
                                           balance_change = -amount,
                                           current_balance = selected_user.balance)
                #добавляем в бд новый транзакт
                session.add(new_transact)
                #применяем изменения в бд
                session.commit()

                #формируем объект для возврата метода
                result = {"paymentId": new_transact.id,
                        "invoiceUrl": f"http://{Config.host_url}:5000/",
                        "cancellable": False}
                #для изменения статуса инвойса после возврата результата из функции
                #создаем новый поток, который будет выполнен через 2 секунды после возврата результата
                thread = Thread(target=update_invoice, 
                                kwargs={"invoiceUuid": data["invoiceUuid"],
                                        "paymentId": new_transact.id})
                #запускаем полученный поток
                thread.start()
                #в случае успешной оплаты, возвращаем успешный результат работы
                return jsonify({"success": True,
                                "errorMsg": "",
                                "result": result,
                                "errors": []
                                })
            #если пользователь существует, но у него недостаочно средств на счету
            else:
                return jsonify({"errorMsg": "На счету недостаточно средств", 
                                "success": False})
        #если пользователь с таким номером телефона отсутсвует у нас в системе
        else:
            #создаем нового пользователя с таким номером телефона
            new_user = User(phone_number = phone)
            #добавляем его в бд
            session.add(new_user)
            #применяем изменения в бд
            session.commit()
            #из-за того, что новый пользователь имеет изанчально нулевой баланс
            #возвращаем сообщение об недостатке баланса на счету
            return jsonify({"errorMsg": "На счету недостаточно средств", 
                            "success": False})
    #в случае непредвиденной ошибки, отпарвяем ошибку
    except Exception as e:
        return jsonify({"errorMsg": "Что-то пошло не так", 
                        "success": False})    


@app.route('/payment/approve', methods=['POST'])
def payment_approve():
    pass


@app.route('/payment/cancel', methods=['POST'])
def payment_cancel():
    pass


@app.route('/payment/refund', methods=['POST'])
def payment_refund():
    pass


#обязательный пост метод для проверки статуса оплаты
#в данной реализации не используется, возвращается успешный ответ
@app.route('/payment/status', methods=['POST'])
def payment_status():
    return jsonify({"errorMsg": "", 
                    "success": True})


@app.teardown_appcontext
def shutdown_session(exception=None):
    #если программа завершилась, то закрываем текущее соединение
    session.remove()


if __name__ == '__main__':
    # Создаем процесс для начисления бонуса в 5% раз в сутки  
    scheduler.add_job(id="Scheduled task",#ID потока
                      func=scheduledTask,#Функцию которую вызывает поток
                      trigger="interval",#Триггер из-за которого будет вызываться поток, в данном случае наступление времени
                      minutes=60*24)#Раз в сутки
    scheduler.start()
    app.run(debug=True, 
            use_reloader=False,
            host=Config.host_url)
