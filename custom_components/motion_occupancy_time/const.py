from datetime import timedelta

DOMAIN = "motion_occupancy_time"
STORAGE_VERSION = 1
STORAGE_KEY = "motion_occupancy_time"
RESCAN_INTERVAL = timedelta(minutes=1)
SAVE_DELAY = timedelta(seconds=10)
SUPPORTED_DEVICE_CLASSES = {"motion", "occupancy", "presence"}
