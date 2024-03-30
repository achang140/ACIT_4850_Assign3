import os
import time
import logging
import logging.config
import uuid
import datetime
import json
import yaml
import connexion
from connexion import NoContent
from pykafka import KafkaClient


if "TARGET_ENV" in os.environ and os.environ["TARGET_ENV"] == "test":
    print("In Test Environment")
    APP_CONF_FILE = "/config/app_conf.yml"
    LOG_CONF_FILE = "/config/log_conf.yml"
else:
    print("In Dev Environment")
    APP_CONF_FILE = "app_conf.yml"
    LOG_CONF_FILE = "log_conf.yml"

with open(APP_CONF_FILE, 'r') as f:
    app_config = yaml.safe_load(f.read())

# External Logging Configuration
with open(LOG_CONF_FILE, 'r') as f:
    log_config = yaml.safe_load(f.read())

logging.config.dictConfig(log_config)
logger = logging.getLogger('basicLogger')
logger.info("App Conf File: %s" % APP_CONF_FILE)
logger.info("Log Conf File: %s" % LOG_CONF_FILE)


CURRENT_ENTRY = 0
max_retries = app_config["events"]["max_retries"]

while CURRENT_ENTRY < max_retries:
    try:
        logger.info(f"Trying to connect to Kafka. Current retry count: {CURRENT_ENTRY}")
        client = KafkaClient(hosts=f"{app_config['events']['hostname']}:{app_config['events']['port']}")

        # First Topic events
        first_topic = client.topics[str.encode(app_config["events"]["topics"][0])]
        first_producer = first_topic.get_sync_producer()

        # Second Topic event_log
        second_topic = client.topics[str.encode(app_config["events"]["topics"][1])]
        second_producer = second_topic.get_sync_producer()

        break

    except:
        logger.error("Connection failed.")
        time.sleep(app_config["events"]["sleep_time"])
        CURRENT_ENTRY += 1


def load(producer_two):
    """ Send Message to Kafka """

    if producer_two is None:
        logger.error("Producer does not exist")
    else:
        ready_msg = {
            "message_info": 
            "Receiver service successfully started and connected to Kafka. Ready to receive messages on RESTful API.",
            "message_code": "0001"
        }
        ready_msg_str = json.dumps(ready_msg)
        producer_two.produce(ready_msg_str.encode('utf-8'))


def book_hotel_room(body):
    """ Receives a hotel room booking event """

    trace_id = uuid.uuid4()
    body["trace_id"] = str(trace_id)

    logger.info("Received event Hotel Room Booking request with a trace id of %s", body["trace_id"])

    # First Topic (events)
    msg = {
        "type": "hotel_room",
        "datetime": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "payload": body
    }
    msg_str = json.dumps(msg)
    first_producer.produce(msg_str.encode('utf-8'))

    logger.info(f"Returned event Hotel Room Booking response (Id: ${body['trace_id']}) with status 201")

    # return NoContent, response.status_code
    return NoContent, 201

def book_hotel_activity(body):
    """ Receives a hotel activity reservation event """

    trace_id = uuid.uuid4()
    body["trace_id"] = str(trace_id)


    logger.info("Received event Hotel Activity Booking request with a trace id of %s", body["trace_id"])

    # First Topic (events)
    msg = {
        "type": "hotel_activity",
        "datetime": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "payload": body
    }
    msg_str = json.dumps(msg)
    first_producer.produce(msg_str.encode('utf-8'))

    logger.info("Returned event Hotel Activity Booking response (Id: %s) with status %d", body["trace_id"], 201)

    # return NoContent, response.status_code
    return NoContent, 201


app = connexion.FlaskApp(__name__, specification_dir="")
app.add_api("openapi.yaml",
            strict_validation=True,
            validate_responses=True)

if __name__ == "__main__":
    load(second_producer)
    app.run(port=8080)
