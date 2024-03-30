import os
import json
import time
import datetime
import logging
import logging.config
import requests

import yaml

import connexion
from connexion import FlaskApp
from flask_cors import CORS

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pytz import timezone
from pykafka import KafkaClient
from apscheduler.schedulers.background import BackgroundScheduler

from base import Base
from stats import Stats



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
logger.info("App Conf File: %s", APP_CONF_FILE)
logger.info("Log Conf File: %s", LOG_CONF_FILE)

db_file_path = app_config["datastore"]["filename"]

DB_ENGINE = create_engine("sqlite:///%s" % app_config["datastore"]["filename"])

if not os.path.exists(db_file_path):
    Base.metadata.create_all(DB_ENGINE)

Base.metadata.bind = DB_ENGINE
DB_SESSION = sessionmaker(bind=DB_ENGINE)

CURRENT_RETRY = 0
max_retries = app_config["events"]["max_retries"]

while CURRENT_RETRY < max_retries:
    try:
        logger.info(f"Trying to connect to Kafka. Current retry count: {CURRENT_RETRY}")
        client = KafkaClient(hosts=f"{app_config['events']['hostname']}:{app_config['events']['port']}")
        topic = client.topics[str.encode(app_config["events"]["topic"])]
        producer = topic.get_sync_producer()

        break

    except:
        logger.error("Connection failed.")
        time.sleep(app_config["events"]["sleep_time"])
        CURRENT_RETRY += 1


def load(only_producer):
    """ Connect to Kafka """
    if only_producer is None:
        logger.error("Producer does not exist")
    else:
        ready_msg = {
            "message_info": "Processing service successfully started and connected to Kafka.",
            "message_code": "0003"
        }
        ready_msg_str = json.dumps(ready_msg)
        only_producer.produce(ready_msg_str.encode('utf-8'))


def get_stats():
    """ Gets Hotel Room and Hotel Activity processsed statistics """

    # Log an INFO message indicating request has started
    logger.info("Request Started")

    # Read in the current statistics from the SQLite database (i.e., the row with the most recent last_update datetime stamp.
    session = DB_SESSION()

    stats = session.query(Stats).order_by(Stats.last_updated.desc()).first()

    # If no stats exist, log an ERROR message and return 404 and the message “Statistics do not exist” OR return empty/default statistics
    if stats is None:
        logger.error("Statistics do not exist")
        return "Statistics do not exist", 404

    vancouver_timezone = timezone('America/Vancouver')
    last_updated_vancouver = stats.last_updated.astimezone(vancouver_timezone)

    # Convert them as necessary into a new Python dictionary such that the structure matches that of your response defined in the openapi.yaml file.
    statistics = {
        "num_hotel_room_reservations": stats.num_hotel_room_reservations,
        "max_hotel_room_ppl": stats.max_hotel_room_ppl,
        "num_hotel_activity_reservations": stats.num_hotel_activity_reservations,
        "max_hotel_activity_ppl": stats.max_hotel_activity_ppl,
        "last_updated": last_updated_vancouver.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    }

    # Log a DEBUG message with the contents of the Python Dictionary
    logger.debug(statistics)

    # Log an INFO message indicating request has completed
    logger.info("Request Completed!")

    session.close()

    # Return the Python dictionary as the context and 200 as the response code
    return statistics, 200

def populate_stats():
    """ Periodically update stats """

    # Log an INFO message indicating periodic processing has started
    logger.info("Start Periodic Processing")

    # Read in the current statistics from the SQLite database (filename defined in your configuration)
    session = DB_SESSION()

    # Query to get all the Stats objects from the database in descending order (from newest to oldest)
    # Note that the first would be the most recent in this case
    stats = session.query(Stats).order_by(Stats.last_updated.desc()).first()

    # - If no stats yet exist, use default values for the stats
    if stats is None:
        stats = Stats(
            num_hotel_room_reservations = 0,
            max_hotel_room_ppl = 0,
            num_hotel_activity_reservations = 0,
            max_hotel_activity_ppl = 0,
            last_updated=datetime.datetime.now()
        )

        session.add(stats)
        session.commit()

    last_updated = stats.last_updated

    # Get the current datetime
    current_datetime = datetime.datetime.now()

    curren_dateime_formatted = current_datetime.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    last_updated_formatted = last_updated.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Query the two GET endpoints from your Data Store Service (using requests.get) to get all new events
    # from the last datetime you requested them (from your statistics) to the current datetime
    hotel_rooms_url = f"{app_config['eventstore']['url']}/booking/hotel-rooms?start_timestamp={last_updated_formatted}&end_timestamp={curren_dateime_formatted}"
    hotel_activities_url = f"{app_config['eventstore']['url']}/booking/hotel-activities?start_timestamp={last_updated_formatted}&end_timestamp={curren_dateime_formatted}"

    event_1_response = requests.get(hotel_rooms_url, timeout=10)
    event_2_response = requests.get(hotel_activities_url, timeout=10)

    event_1_res_json = event_1_response.json()
    event_2_res_json = event_2_response.json()

    # - Log an INFO message with the number of events received
    if event_1_response.status_code == 200 and event_2_response.status_code == 200:
        logger.info(f"Received {len(event_1_res_json)} Hotel Room Reservation events and {len(event_2_res_json)} Hotel Activity Reservation events")

    # - Log an ERROR message if you did not get a 200 response code
    else:
        logger.error(f'''Failed to retrieve events from Hotel Room and Hotel Activity Reservations:

                        Hotel Rooms Error: {event_1_response.text},

                        Hotel Activities Error: {event_2_response.text}''')
        return

    # Based on the new events from the Data Store Service:
    # Calculate your updated statistics

    max_hotel_room_ppl_sql = stats.max_hotel_room_ppl

    max_hotel_activity_ppl_sql = stats.max_hotel_activity_ppl


    if len(event_1_res_json):
        max_hotel_room_ppl_json = max(event_1_res_json, key=lambda event1: event1["num_of_people"])["num_of_people"]

        if max_hotel_room_ppl_json > max_hotel_room_ppl_sql:
            new_max_hotel_room_ppl = max_hotel_room_ppl_json
        else:
            new_max_hotel_room_ppl = max_hotel_room_ppl_sql
    else:
        new_max_hotel_room_ppl = max_hotel_room_ppl_sql


    if len(event_2_res_json):
        max_hotel_activity_ppl_json = max(event_2_res_json, key=lambda event2: event2["num_of_people"])["num_of_people"]

        if max_hotel_activity_ppl_json > max_hotel_activity_ppl_sql:
            new_max_hotel_activity_ppl = max_hotel_activity_ppl_json
        else:
            new_max_hotel_activity_ppl = max_hotel_activity_ppl_sql
    else:
        new_max_hotel_activity_ppl = max_hotel_activity_ppl_sql

    new_num_hotel_room_reservations = stats.num_hotel_room_reservations + len(event_1_res_json)
    new_num_hotel_activity_reservations = stats.num_hotel_activity_reservations + len(event_2_res_json)

    new_stats = Stats(
        num_hotel_room_reservations=new_num_hotel_room_reservations,
        max_hotel_room_ppl=new_max_hotel_room_ppl,
        num_hotel_activity_reservations=new_num_hotel_activity_reservations,
        max_hotel_activity_ppl=new_max_hotel_activity_ppl,
        last_updated=current_datetime
    )

    # Write the updated statistics to the SQLite database file (filename defined in your configuration)
    session.add(new_stats)

    # Log a DEBUG message for each event processed that includes the trace_id
    if len(event_1_res_json):
        trace_ids = [event_1["trace_id"] for event_1 in event_1_res_json]
        logger.debug(f"Processed Hotel Room Reservation Event Trace IDs: {', '.join(trace_ids)}")

    if len(event_2_res_json):
        trace_ids = [event_2["trace_id"] for event_2 in event_2_res_json]
        logger.debug(f"Processed Hotel Activity Reservation Event Trace IDs: {', '.join(trace_ids)}")

    # Log a DEBUG message with your updated statistics values
    logger.debug(f"Num Hotel Room Reservations: {new_stats.num_hotel_room_reservations} \n"
                 f"Max Hotel Room People: {new_stats.max_hotel_room_ppl} \n" 
                 f"Num Hotel Activity Reservations: {new_stats.num_hotel_activity_reservations} \n"
                 f"Max Hotel Activity People: {new_stats.max_hotel_activity_ppl}\n"
                 f"Last Updated: {new_stats.last_updated.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'}")

    # Log an INFO message indicating period processing has ended
    logger.info("End Periodic Processing")

    # On periodic processing if it receives more than a configurable number of messages.
    # The default is 25 for this configurable value. The code for this message is 0004.
    threshold = app_config["events"]["event_threshold"]
    total_events_received = len(event_1_res_json) + len(event_2_res_json)

    if total_events_received > threshold:
        msg = {
            "message_info": "Total events received exceeded threshold ({threshold})",
            "message_code": "0004"
        }
        msg_str = json.dumps(msg)
        producer.produce(msg_str.encode('utf-8'))

    session.commit()
    session.close()


def init_scheduler():
    sched = BackgroundScheduler(daemon=True, timezone=timezone('America/Vancouver'))
    sched.add_job(populate_stats,
                  'interval',
                   seconds=app_config['scheduler']['period_sec'])
    sched.start()


app = connexion.FlaskApp(__name__, specification_dir="")
app.add_api("openapi.yaml", strict_validation=True, validate_responses=True)

CORS(app.app)
app.app.config["CORS_HEADERS"] = "Content-Type"


if __name__ == "__main__":
    init_scheduler()
    load(producer)
    app.run(port=8100)
