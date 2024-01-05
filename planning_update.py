import sys
import requests
import json
import datetime
import re
import logging
import logging.config
import dataclasses
from dateutil.relativedelta import *

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": "%(asctime)s %(levelname)s %(message)s %(module)s",
        }
    },
    "handlers": {
        "stdout": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "log.txt",
            "formatter": "json",
            "encoding": "utf8",
            "maxBytes": 1024 * 1024 * 128,
            "backupCount": 3,
        }
    },
    "loggers": {"": {"handlers": ["stdout"], "level": "DEBUG"}},
}

list_names = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
]

dateformat = "%Y-%m-%dT%H:%M:%S.000Z"

logging.config.dictConfig(LOGGING)
logger = logging.getLogger(__name__)


current_time = datetime.datetime.utcnow()


@dataclasses.dataclass
class List:
  name: str
  identifier: str


@dataclasses.dataclass
class Card:
  name: str
  identifier: str
  due: datetime.datetime
  period_len: int
  period_type: str


@dataclasses.dataclass
class CardToSort:
  name: str
  identifier: str
  due: datetime.datetime


class Requester:
  def __init__(self, filename):
    self.headers = { "Accept": "application/json" }
    with open(filename, "r") as fin:
      self.board_id, trello_api_key, trello_api_token = [x.strip("\n") for x in fin.readlines() if x]
    logger.info(f"Prepared data to access rest API using config {filename}.")
    self.query = {
      'key': trello_api_key,
      'token': trello_api_token
    }

  def get_lists(self):
    return json.loads(requests.request(
      "GET",
      f"https://api.trello.com/1/boards/{self.board_id}/lists",
      headers=self.headers,
      params=self.query
    ).text)
  
  def get_cards(self, list_id):
    return json.loads(requests.request(
        "GET",
        f"https://api.trello.com/1/lists/{list_id}/cards",
        headers=self.headers,
        params=self.query
      ).text)
  
  def trello_put(self, url):
    requests.request(
      "PUT",
      url,
      headers=self.headers,
        params=self.query
    )


def get_lists(requester):
  try:
    lists_response = requester.get_lists()
  except Exception as e:
    logger.critical(f"Can't get lists {e}")
    return []
  lists = [
    List(l["name"], l["id"]) for l in lists_response if l["name"] in list_names
  ]
  logger.info(f"Received lists: {lists}")
  if len(list_names) != len(lists):
    logger.error(f"Not all necessary lists were received. Received {lists}, expected {list_names}")
  return lists


def get_cards(requester, l):
  try:
    cards = requester.get_cards(l.identifier)
  except Exception as e:
    logger.critical(f"Failed to receive cards for list ({l}): {e}")
    return []
  logger.info(f"{len(cards)} cards found.")
  return cards


def filter_out_cards(cards):
  def is_regular(card):
    return any(label.get("name", "") == "Regular" for label in card.get("labels", [])) 

  cards_to_process = []
  for card in cards:
    logger.info(f"Check card {card['name']}")
    if not is_regular(card):
      logger.info(f"Filtered out, because is not regular {card.get('labels', [])}.")
      continue
    try:
      due = datetime.datetime.strptime(card["due"], dateformat)
    except Exception as e:
      logger.error(f"Can't parse due date {card['due']}. {e}")
      continue
    if due >= current_time:
      logger.info(f"Filtered out, because of due date {due}. Now {current_time}")
      continue
    try:
      period_len, period_type = re.match(r"(\d+) (\w+)", card["desc"]).group(1, 2)
      period_len = int(period_len)
    except Exception as e:
      logger.error(f"Can't parse period from description {card['desc']}. {e}")
    cards_to_process.append(Card(card["name"], card["id"], due, period_len, period_type))
  return cards_to_process


def process_card(requester, card, lists):
  try:
    due = card.due
    while due < current_time:
      due += relativedelta(**{card.period_type: card.period_len})
  except Exception as e:
    logger.error(f"Failed to calculate a new due date for card {card}. {e}")
    return
  try:
    list_name = list_names[due.weekday()]
    for l in lists:
      if l.name == list_name:
        list_id = l.identifier
        break
    else:
      logger.error(f"Can't find a target list for card {card} with due date {due}")
      return
  except Exception as e:
    logger.error(f"Failed to find a target list id for a card {card}. {e}")
    return
  try:
    requester.trello_put(f"https://api.trello.com/1/cards/{card.identifier}?dueComplete=false&due={due.strftime(dateformat)}&idList={list_id}")
  except Exception as e:
    logger.error(f"Failed to update due date for card {card}")
    return
  logger.info(f"Due date for card ({card.name}, {card.identifier}) updated from {card.due} to {due}")


def construct_cards_to_order(cards):
  return [CardToSort(card["name"], card["id"], datetime.datetime.strptime(card["due"], dateformat)) for card in cards]


def sort_cards(cards):
  return sorted(cards, key=lambda x: x.due)


def set_card_order(requester, card):
  try:
    requester.trello_put(f"https://api.trello.com/1/cards/{card.identifier}?pos=bottom")
  except Exception as e:
    logger.error(f"Failed to push back card {card}")


def main():
  logger.info(f"Start to prepare requester using config {sys.argv[1]}")
  requester = Requester(sys.argv[1])

  lists = get_lists(requester)
  logger.info(f"Started to update due dates")
  for l in lists:
    logger.info(f"Start to process list: {l}")
    cards = filter_out_cards(get_cards(requester, l))
    for card in cards:
      logger.info(f"Start to process card: ('{card.name}', {card.identifier})")
      process_card(requester, card, lists)
  logger.info(f"Start to sort cards")
  for l in lists:
    logger.info(f"Start to process list: {l}")
    cards = construct_cards_to_order(get_cards(requester, l))
    logger.info(f"Cards received and constructed")
    cards = sort_cards(cards)
    logger.info(f"Cards sorted")
    for card in cards:
      set_card_order(requester, card)
    logger.info(f"Card order set")


main()

