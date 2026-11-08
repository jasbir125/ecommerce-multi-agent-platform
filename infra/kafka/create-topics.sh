#!/bin/bash
# Creates every topic in topics.txt plus a ".dlt" dead-letter topic for each.
# Safe to re-run (--if-not-exists).
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP_SERVER:-kafka:19092}"
KAFKA_TOPICS=/opt/kafka/bin/kafka-topics.sh

create() {
  echo "Creating topic $1 (partitions=$2)"
  "$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
    --topic "$1" --partitions "$2" --replication-factor 1
}

while IFS= read -r line; do
  [[ -z "$line" || "$line" =~ ^# ]] && continue      # skip blanks and comments
  name="${line%%:*}"
  partitions="${line##*:}"
  [[ "$name" == "$partitions" ]] && partitions=3     # no ":N" -> default
  create "$name" "$partitions"
  create "${name}.dlt" 1                              # dead-letter topic
done < /scripts/topics.txt

echo "Topics present:"
"$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --list
