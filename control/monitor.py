from argparse import ArgumentError
import ssl
from django.db.models import Avg, Min, Max, Count
from datetime import timedelta, datetime
from receiver.models import Data, Measurement
import paho.mqtt.client as mqtt
import schedule
import time
from django.conf import settings

client = mqtt.Client(settings.MQTT_USER_PUB)
previous_sample_values = {}


def custom_analyze_data():
    # Consulta todos los datos de la última hora, los agrupa por estación y variable
    # Compara el promedio con los valores límite que están en la base de datos para esa variable.
    # Si el promedio se excede de los límites, se envia un mensaje de alerta.

    print("Calculando nuevas alertas...")

    data = Data.objects.filter(
        base_time__gte=datetime.now() - timedelta(hours=1))
    aggregation = data.annotate(check_last_values=Max('values')) \
        .select_related('station', 'measurement') \
        .select_related('station__user', 'station__location') \
        .select_related('station__location__city', 'station__location__state',
                        'station__location__country') \
        .values('check_last_values', 'station__user__username',
                'measurement__name',
                'measurement__max_value',
                'measurement__min_value',
                'station__location__city__name',
                'station__location__state__name',
                'station__location__country__name')  
    

    alerts = 0
    for item in aggregation:
        alert_1 = False
        alert_2 = False

        variable = item["measurement__name"]
        max_value = item["measurement__max_value"] or 0
        min_value = item["measurement__min_value"] or 0

        country = item['station__location__country__name']
        state = item['station__location__state__name']
        city = item['station__location__city__name']
        user = item['station__user__username']

        if len(previous_sample_values) > 0 and variable == "temperatura":
            previous_sample_length = len(previous_sample_values[f"{user}|{city}|{state}|{country}|{variable}"])
            previous_sample_last_value = previous_sample_values[f"{user}|{city}|{state}|{country}|{variable}"][-1]
            current_sample_values = item['check_last_values'][previous_sample_length:]
            print('current sample values = ', current_sample_values)
            print("*********************************************************")
            print('previous sample last value = ', previous_sample_last_value)
            print('current sample last value = ', item['check_last_values'][-1])
            m = (item['check_last_values'][-1] - previous_sample_last_value)/30
            if m > 1/2400:
                alert_1 = True
            if len(current_sample_values) > 0 and sum(current_sample_values)/len(current_sample_values) > 27:
                alert_2 = True

        if alert_1:
            message = "ALERT {} {}".format(variable, 'Ventilador')
            topic = '{}/{}/{}/{}/in'.format(country, state, city, user)
            print(datetime.now(), "Sending alert to {} {}".format(topic, variable))
            client.publish(topic, message)
            alerts += 1

        if alert_2:
            message = "ALERT {} {}".format(variable, 'Aire Acondicionado')
            topic = '{}/{}/{}/{}/in'.format(country, state, city, user)
            print(datetime.now(), "Sending alert to {} {}".format(topic, variable))
            client.publish(topic, message)
            alerts += 1
        
        if variable == 'temperatura':
            previous_sample_values[f"{user}|{city}|{state}|{country}|{variable}"] = item['check_last_values']

    print(len(aggregation), "dispositivos revisados")
    print(alerts, "nuevas alertas enviadas")


# def analyze_data():
#     # Consulta todos los datos de la última hora, los agrupa por estación y variable
#     # Compara el promedio con los valores límite que están en la base de datos para esa variable.
#     # Si el promedio se excede de los límites, se envia un mensaje de alerta.

#     print("Calculando alertas...")

#     data = Data.objects.filter(
#         base_time__gte=datetime.now() - timedelta(hours=1))
#     aggregation = data.annotate(check_value=Avg('avg_value')) \
#         .select_related('station', 'measurement') \
#         .select_related('station__user', 'station__location') \
#         .select_related('station__location__city', 'station__location__state',
#                         'station__location__country') \
#         .values('check_value', 'station__user__username',
#                 'measurement__name',
#                 'measurement__max_value',
#                 'measurement__min_value',
#                 'station__location__city__name',
#                 'station__location__state__name',
#                 'station__location__country__name')
#     alerts = 0
#     for item in aggregation:
#         alert = False

#         variable = item["measurement__name"]
#         max_value = item["measurement__max_value"] or 0
#         min_value = item["measurement__min_value"] or 0

#         country = item['station__location__country__name']
#         state = item['station__location__state__name']
#         city = item['station__location__city__name']
#         user = item['station__user__username']

#         if item["check_value"] > max_value or item["check_value"] < min_value:
#             alert = True

#         if alert:
#             message = "ALERT {} {} {}".format(variable, min_value, max_value)
#             topic = '{}/{}/{}/{}/in'.format(country, state, city, user)
#             print(datetime.now(), "Sending alert to {} {}".format(topic, variable))
#             client.publish(topic, message)
#             alerts += 1

#     print(len(aggregation), "dispositivos revisados")
#     print(alerts, "alertas enviadas")


def on_connect(client, userdata, flags, rc):
    '''
    Función que se ejecuta cuando se conecta al bróker.
    '''
    print("Conectando al broker MQTT...", mqtt.connack_string(rc))


def on_disconnect(client: mqtt.Client, userdata, rc):
    '''
    Función que se ejecuta cuando se desconecta del broker.
    Intenta reconectar al bróker.
    '''
    print("Desconectado con mensaje:" + str(mqtt.connack_string(rc)))
    print("Reconectando...")
    client.reconnect()


def setup_mqtt():
    '''
    Configura el cliente MQTT para conectarse al broker.
    '''

    print("Iniciando cliente MQTT...", settings.MQTT_HOST, settings.MQTT_PORT)
    global client
    try:
        client = mqtt.Client(settings.MQTT_USER_PUB)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect

        if settings.MQTT_USE_TLS:
            client.tls_set(ca_certs=settings.CA_CRT_PATH,
                           tls_version=ssl.PROTOCOL_TLSv1_2, cert_reqs=ssl.CERT_NONE)

        client.username_pw_set(settings.MQTT_USER_PUB,
                               settings.MQTT_PASSWORD_PUB)
        client.connect(settings.MQTT_HOST, settings.MQTT_PORT)

    except Exception as e:
        print('Ocurrió un error al conectar con el bróker MQTT:', e)


def start_cron():
    '''
    Inicia el cron que se encarga de ejecutar la función analyze_data cada 5 minutos.
    '''
    print("Iniciando cron...")
    # schedule.every(5).minutes.do(analyze_data)
    schedule.every(30).seconds.do(custom_analyze_data)
    print("Servicio de control iniciado")
    while 1:
        schedule.run_pending()
        time.sleep(1)
