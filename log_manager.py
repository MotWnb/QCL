import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logger():
    # 创建日志器
    logger = logging.getLogger('QCL')
    logger.setLevel(logging.DEBUG)

    # 处理日志文件
    log_dir = 'QCL'
    log_file = os.path.join(log_dir, 'debug.log')
    old_log_file = os.path.join(log_dir, 'log.old')

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    if os.path.exists(log_file):
        if os.path.exists(old_log_file):
            os.remove(old_log_file)
        os.rename(log_file, old_log_file)

    # 创建文件处理器
    file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024*10, backupCount=5)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)

    # 将处理器添加到日志器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

# 初始化日志器
logger = setup_logger()
