"""
Simple Flask application connected to MongoDB.

Endpoints:
    GET  /      -> Welcome message with the current date and time
    POST /data  -> Insert a JSON object into MongoDB
    GET  /data  -> Return all stored documents (without MongoDB's _id field)
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Load variables from a local .env file (if one exists).
# In Docker/Kubernetes the variables are provided by the environment instead,
# and load_dotenv() never overrides variables that are already set.
load_dotenv()

# Read the MongoDB connection string from the environment.
# Credentials live in MONGODB_URI, never in this source file.
MONGODB_URI = os.environ.get("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI environment variable is not set. "
        "Copy .env.example to .env and fill in your connection string."
    )

# Database and collection names can be set from the environment
# (e.g. a Kubernetes ConfigMap). The defaults keep local development working.
DATABASE_NAME = os.getenv("MONGODB_DATABASE", "flask_db")
COLLECTION_NAME = os.getenv("MONGODB_COLLECTION", "data")

app = Flask(__name__)

# Create the MongoDB client once and reuse it for every request.
# serverSelectionTimeoutMS makes requests fail after 5 seconds
# instead of hanging if MongoDB is unreachable.
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client[DATABASE_NAME]
collection = db[COLLECTION_NAME]


@app.route("/", methods=["GET"])
def index():
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Welcome to the Flask app! The current time is: {current_time}"


@app.route("/data", methods=["POST"])
def insert_data():
    # silent=True returns None instead of raising an error for invalid JSON.
    data = request.get_json(silent=True)

    if data is None:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "JSON body must be an object, e.g. {\"name\": \"value\"}"}), 400

    if not data:
        return jsonify({"error": "JSON object must not be empty"}), 400

    try:
        # insert_one() adds an _id key to the dict it receives,
        # so we pass a copy to keep the original data unchanged.
        collection.insert_one(dict(data))
    except PyMongoError as error:
        app.logger.error("Failed to insert data: %s", error)
        return jsonify({"error": "Database error, could not save data"}), 503

    return jsonify({"status": "Data inserted"}), 201


@app.route("/data", methods=["GET"])
def get_data():
    try:
        # The projection {"_id": 0} tells MongoDB not to return the _id field.
        documents = list(collection.find({}, {"_id": 0}))
    except PyMongoError as error:
        app.logger.error("Failed to read data: %s", error)
        return jsonify({"error": "Database error, could not read data"}), 503

    return jsonify(documents), 200


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method not allowed"}), 405


if __name__ == "__main__":
    # 0.0.0.0 makes the app reachable from outside the container.
    app.run(host="0.0.0.0", port=5000)
