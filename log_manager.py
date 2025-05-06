import logging
import os
from logging.handlers import RotatingFileHandler

class ILoggerSetup:
    def setup_logger(self):
        pass

class LoggerSetup(ILoggerSetup):
    def setup_logger(self):
        qcl_logger = logging.getLogger('QCL')
        qcl_logger.setLevel(logging.DEBUG)
        log_dir = 'QCL'
        log_file = os.path.join(log_dir, 'debug.log')
        old_log_file = os.path.join(log_dir, 'log.old')
        if not os.path.exists(log_dir): os.makedirs(log_dir)
        if os.path.exists(log_file):
            if os.path.exists(old_log_file): os.remove(old_log_file)
            os.rename(log_file, old_log_file)
        file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024*10, backupCount=5)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter('%(message)s')
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        qcl_logger.addHandler(file_handler)
        qcl_logger.addHandler(console_handler)
        return qcl_logger

# 初始化日志器
logger_setup = LoggerSetup()
logger = logger_setup.setup_logger()