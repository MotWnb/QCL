import json


def get_config():
    with open('config.json', 'r') as f:
        return json.load(f)