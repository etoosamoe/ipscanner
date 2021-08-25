import time
import os
from dotenv import load_dotenv
from datetime import datetime
from threading import Thread #multithreading pinging
from config import * #use settings from config.py
import requests, json
import pymysql
import subprocess
import socket #dns resolve
from pymysql.cursors import DictCursor
try:
    import queue
except ImportError:
    import Queue as queue

# пингуем только диапазоны:
# 10.1.1.0/24+
# 10.1.12.0/23+
# 10.1.14.0/23+
# 10.1.65.0/24+
# 10.1.67.0/24+
# 192.168.1.0/24+
# адреса для пинга прописаны в базе


# функция для многопоточного пинга хостов
def async_ping(i, q):
    while True:
        ip = q.get()
        ping_reply = subprocess.run(["ping","-c","2","-W","1", ip],stderr=subprocess.PIPE, stdout=subprocess.PIPE) # 2 пинга, 1 секунда таймаута
        result =""
        if ping_reply.returncode == 0:
            if ("unreachable" in str(ping_reply.stdout)):
                result = False
            else:
                result= True
        elif ping_reply.returncode == 1:
            result= False
        else:
            result= False
        #print('Ping', ip, '=', result)
        status_dict.update({ip:result})
        q.task_done()

# основная функция для обновления данных об ip-адресах
def db_update(statusdict):
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    print(datetime.now(), 'Starting DB update')
    with connection:
        cur = connection.cursor()
        curdate = int(time.time())
        print(datetime.now(), 'Current timestamp:', curdate)
        for kv in statusdict:
            if statusdict[kv] == True: # если адрес пингуется - обновляем last_alive_date
                #print(kv, ':', statusdict[kv]) #debug
                datetm = time.strftime('%Y-%m-%d %H:%M:%S')
                sql = "UPDATE `test-ip-addresses` SET `last_status` = %s, `last_alive_date` = %s, `last_scan_date` = %s WHERE `ip_address` = %s"
                cur.execute(sql, (str(statusdict[kv]), curdate, curdate, kv))
                #print('{} {} {} {}'.format(kv, str(statusdict[kv]), datetm, datetm))
                
            else: # если адрес НЕ пингуется - не обновляем last_alive_date
                #print(kv, ':', statusdict[kv]) #debug
                datetm = time.strftime('%Y-%m-%d %H:%M:%S')
                sql = "UPDATE `test-ip-addresses` SET `last_status` = %s, `last_scan_date` = %s WHERE `ip_address` = %s"
                cur.execute(sql, (str(statusdict[kv]), curdate, kv))
                #print('{} {} {}'.format(kv, str(statusdict[kv]), datetm))
        connection.commit()
    print(datetime.now(), 'DB update finished')

# функция для получаения списка подсетей из соседней таблицы
def get_nets(): 
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        netdict = {}
        sql = "SELECT `net`, `prefix` FROM `test-ip-nets`"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            netdict.update({row["net"]:row["prefix"]})
        #connection.commit()
        return netdict

def get_db_id():
    db_id_list = []
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        sql = "SELECT `ip_address_id` FROM `test-ip-addresses`"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            db_id_list.append(row["ip_address_id"])
        #print(db_id_set)
        return db_id_list

def get_nb_id(): # получить not-reserved адреса
    nb_id_list = []
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        netdict = {}
        sql = "SELECT `net`, `prefix` FROM `test-ip-nets`"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            netdict.update({row["net"]:row["prefix"]})
        #print(netdict)
    for i in netdict:
        net = str(i) + '/' + str(netdict[i])
        r = requests.get(f'{URL}api/ipam/ip-addresses/?parent={net}&limit=1000&status__n=reserved', headers=headers)
        text = json.loads(r.text)
        for ip in text[u'results']:
            nb_id_list.append(ip[u'id'])
    return nb_id_list
        

def nb_db_sync(db_id,nb_id): # функция проверки различий между БД и НБ
    # set - set
    print(datetime.now(), 'NB <--> DB sync started.')
    db_diff_set = set(nb_id) - set(db_id)
    nb_diff_set = set(db_id) - set(nb_id)
    db_diff_list = list(db_diff_set)
    nb_diff_list = list(nb_diff_set)
    if db_diff_list:
        print(datetime.now(), 'Актив\Депрек в Нетбоксе, но нет в ДБ:',db_diff_list)
        connection = pymysql.connect(
        host=f'{DB_HOST}',
        user=f'{DB_USER}',
        password=f'{DB_PASS}',
        db=f'{DB_DATABASE}',
        charset='utf8mb4',
        cursorclass=DictCursor
        )
        with connection:
            cur = connection.cursor()
            for i in db_diff_list:
                r = requests.get(f'{URL}api/ipam/ip-addresses/{i}/', headers=headers)
                text = json.loads(r.text)
                sync_ip = text[u'address']
                splitted_sync_ip = sync_ip.split('/')
                sql = "INSERT INTO `test-ip-addresses` (`ip_address`, `ip_prefix`, `ip_address_id`) VALUES (%s, %s, %s)"
                print(datetime.now(), sql, splitted_sync_ip[0], splitted_sync_ip[1], text[u'id'])
                cur.execute(sql, (splitted_sync_ip[0], splitted_sync_ip[1], text[u'id']))
                connection.commit()
    if nb_diff_list:
        print(datetime.now(), 'Резервед в нетбоксе, но есть в ДБ:',nb_diff_list)
        connection = pymysql.connect(
        host=f'{DB_HOST}',
        user=f'{DB_USER}',
        password=f'{DB_PASS}',
        db=f'{DB_DATABASE}',
        charset='utf8mb4',
        cursorclass=DictCursor
        )
        with connection:
            cur = connection.cursor()
            for i in nb_diff_list:
                sql = 'DELETE FROM `test-ip-addresses` WHERE `ip_address_id` = %s'
                print(datetime.now(), sql, i)
                cur.execute(sql, i)
                connection.commit()
    print(datetime.now(), 'NB <--> DB sync finished.')

def nb_get_reserved_ip(): # получает список зарезервированных вручную адресов
    ipkv = {}
    r = requests.get(f'{URL}api/ipam/ip-addresses/?status=reserved', headers=headers)
    #print(r.text)
    result = json.loads(r.text)
    for ip in result[u'results']:
        ipkv.update({ip[u'address']:ip[u'id']})
    return ipkv

def db_delete_reserved(ipkv): # удаляет список зарезервированных адресов из БД (принимает словарь)
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        for ip in ipkv:
            #print(ipkv, ipkv[ip])
            sql = 'DELETE FROM `test-ip-addresses` WHERE `ip_address_id` = %s'
            print(sql,ipkv[ip],ip)
            cur.execute(sql, (ipkv[ip]))
        connection.commit()

def get_all_ip():
    db_ip_list = []
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        sql = "SELECT `ip_address` FROM `test-ip-addresses`"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            db_ip_list.append(row["ip_address"])
        #print(db_id_set)
        return db_ip_list

def get_db_old_id_status():
    db_id_old_status_kv = {}
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        sql = "SELECT `ip_address_id`, `last_status` FROM `test-ip-addresses` WHERE `last_scan_date` - `last_alive_date` >= 172800"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            db_id_old_status_kv.update({row["ip_address_id"]:row["last_status"]})
        return db_id_old_status_kv

def get_db_true_id_status():
    db_id_true_status_kv = {}
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        sql = "SELECT `ip_address_id`, `last_status` FROM `test-ip-addresses` WHERE `last_status` = 'True'"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            db_id_true_status_kv.update({row["ip_address_id"]:row["last_status"]})
        return db_id_true_status_kv

def get_nb_id_status(): 
    nb_id_status_kv = {}
    connection = pymysql.connect(
    host=f'{DB_HOST}',
    user=f'{DB_USER}',
    password=f'{DB_PASS}',
    db=f'{DB_DATABASE}',
    charset='utf8mb4',
    cursorclass=DictCursor
    )
    with connection:
        cur = connection.cursor()
        netdict = {}
        sql = "SELECT `net`, `prefix` FROM `test-ip-nets`"
        cur.execute(sql)
        rows = cur.fetchall()
        for row in rows:
            netdict.update({row["net"]:row["prefix"]})
        #print(netdict)
    for i in netdict:
        net = str(i) + '/' + str(netdict[i])
        r = requests.get(f'{URL}api/ipam/ip-addresses/?parent={net}&limit=1000&status__n=reserved', headers=headers)
        text = json.loads(r.text)
        for ip in text[u'results']:
            #print(ip[u'custom_fields'].get('alive'))
            nb_id_status_kv.update({ip[u'id']:ip[u'custom_fields'].get('alive')})
    return nb_id_status_kv

def nb_update_status():
    dbid = get_db_old_id_status()
    dbid_true = get_db_true_id_status()
    nbid = get_nb_id_status()
    diff = {}
    nb_diff = {}

    for k in dbid:
        if dbid.get(k) != str(nbid.get(k)):
            diff.update({k:dbid.get(k)})
    for b in dbid_true:
        if dbid_true.get(b) != str(nbid.get(b)):
            nb_diff.update({b:dbid_true.get(b)})
    if not diff:
        print(datetime.now(), 'Nothing to update')
    else:
        print('Diff: ',diff)
        for row in diff:
            if diff.get(row) == 'True':
                payload = {"status": "active", "custom_fields": {"alive": True}}
                #print(payload)
                addressid = row
                r = requests.patch(f'{URL}api/ipam/ip-addresses/{addressid}/', headers=headers, json=payload)
                print(datetime.now(), 'Updated', addressid, 'with', payload, '- Status is', r.status_code)
            elif diff.get(row) == 'False':
                payload = {"status": "deprecated", "custom_fields": {"alive": False}}
                #print(payload)
                addressid = row
                r = requests.patch(f'{URL}api/ipam/ip-addresses/{addressid}/', headers=headers, json=payload)
                print(datetime.now(), 'Updated id:', addressid, 'with', payload, '- Status code is', r.status_code)
        notification(diff, False)
    if not nb_diff:
        print(datetime.now(), 'Nothing to update')
    else:
        for row in nb_diff:
            payload = {"status": "active", "custom_fields": {"alive": True}}
            #print(payload)
            addressid = row
            r = requests.patch(f'{URL}api/ipam/ip-addresses/{addressid}/', headers=headers, json=payload)
            print(datetime.now(), 'Updated', addressid, 'with', payload, '- Status is', r.status_code)
        notification(nb_diff, True)

def notification(kv, alive):
    string = ""
    if alive == True:
        status = "появился в сети."
    else:
        status = "недоступен более двух дней."
    for i in kv:
        r = requests.get(f'{URL}api/ipam/ip-addresses/{i}/', headers=headers)
        text = json.loads(r.text)
        ipaddrwpref = text[u'address'].split('/')
        try:
            string += f"IP [{text[u'address']}]({URL}ipam/ip-addresses/{i}/) ({socket.gethostbyaddr(ipaddrwpref[0])[0]}) {status}\n"
        except Exception as e:
            string += f"IP [{text[u'address']}]({URL}ipam/ip-addresses/{i}/) {status}\n"
    try:
        data = {
                "chat_id": CHAT_ID,
                "disable_web_page_preview": 'true',
                "parse_mode": "Markdown",
                "text": string
        }
        tg_send = requests.post(TG_URL, data=data)
    except Exception as e:
        print(datetime.now(), e)

### ENV VARS
load_dotenv()
CHAT_ID = os.getenv("CHAT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN")

TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
status_dict = {}
ips = []
prefixes = {}
nets = []
netskv = get_nets() # формируем список подсетей
net12prefix = '24'
headers = {'Authorization': f'Token {NETBOX_TOKEN}', 'Content-Type': 'application/json'}
time_stamp = time.strftime("%Y-%m-%d %H:%M:%S")

num_threads = 50 #количество потоков
ips_q = queue.Queue()

nb_db_sync(get_db_id(),get_nb_id())

print(datetime.now(), 'Starting ping with', num_threads, 'workers.')
for i in range(num_threads):
    worker = Thread(target=async_ping, args=(i, ips_q))
    worker.setDaemon(True)
    worker.start()

for ip in get_all_ip():
    ips_q.put(ip)
ips_q.join() #запускаем пинг, результат пишется в status_dict
print(datetime.now(), 'Ping finished')

db_update(status_dict) #обновляем данные в БД

nb_update_status()

