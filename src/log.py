from loguru import logger
import os, sys

def setup_logging():
    """
    Configures loguru logging based on environment variables.

    Environment Variables:
    - LOG_LEVEL: Severity level (DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL). Default: INFO.
    - LOG_FORMAT: Custom format string for logs.
    - LOG_SERIALIZE: Set to "true" to output logs in JSON format.
    - LOG_ENQUEUE: Set to "true" to make logging thread-safe and non-blocking. Default: true.
    """

    # Remove default handler
    logger.remove()

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv(
        "LOG_FORMAT",
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    log_serialize = os.getenv("LOG_SERIALIZE", "false").lower() == "true"
    log_enqueue = os.getenv("LOG_ENQUEUE", "true").lower() == "true"

   # Add stdout handler
    logger.add(
        sys.stdout,
        level=log_level,
        format=log_format,
        serialize=log_serialize,
        colorize=True,
        enqueue=log_enqueue
    )
    logger.info(f"Logging initialized with level: {log_level}")