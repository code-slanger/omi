#!/usr/bin/env bash
# omi.sh — Pi utility commands
# Usage: ./omi.sh photos | logs | logs-live

PI="lucho@192.168.0.27"
LOCAL_DIR="/Users/mitchumbailey/operational-lair/omi"
PHOTO_DIR="/home/lucho/omi/data/uploads/default/image"
CONTAINER="pi-nano-claw-1"

case "$1" in
  photos)
    echo "Copying last 10 photos from Pi..."
    ssh "$PI" "ls -t $PHOTO_DIR/*.jpg | head -10" | xargs -I{} scp "$PI":{} "$LOCAL_DIR/"
    echo "Done. Photos saved to $LOCAL_DIR"
    ;;
  logs)
    echo "Last 50 transcripts:"
    ssh "$PI" "docker logs $CONTAINER 2>&1 | grep 'Transcript'" | tail -50
    ;;
  logs-live)
    echo "Streaming live transcription logs (Ctrl+C to stop)..."
    ssh "$PI" "docker logs -f $CONTAINER 2>&1 | grep --line-buffered 'Transcript\|Ignored'"
    ;;
  *)
    echo "Usage: $0 {photos|logs|logs-live}"
    exit 1
    ;;
esac
