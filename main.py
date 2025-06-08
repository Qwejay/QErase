import sys
import os
import random
import ctypes
import subprocess
import win32gui
import win32con
import logging
import traceback
import string
import psutil
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, 
                            QWidget, QFileDialog, QLabel, QComboBox, QProgressBar,
                            QHBoxLayout, QFrame, QSizePolicy, QListWidget, QListWidgetItem,
                            QMenu, QScrollArea, QScrollBar, QMessageBox)
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QSettings, QSize, QPoint, QTimer
from PySide6.QtGui import QFont, QIcon, QColor, QAction, QPainter, QPen
import time

# 配置日志记录
def setup_logging():
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        log_file = os.path.join(log_dir, f"qerase_{datetime.now().strftime('%Y%m%d')}.log")
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
    except Exception as e:
        print(f"设置日志记录失败: {str(e)}")

def log_exception(e):
    """记录异常信息"""
    logging.error(f"发生错误: {str(e)}")
    logging.error(f"错误详情: {traceback.format_exc()}")

def is_valid_file_path(file_path):
    """验证文件或文件夹路径是否有效"""
    try:
        if os.path.isfile(file_path):
            return os.access(file_path, os.R_OK | os.W_OK)
        elif os.path.isdir(file_path):
            return os.access(file_path, os.R_OK | os.W_OK)
        return False
    except Exception as e:
        log_exception(e)
        return False

def generate_random_filename(length=32):
    """生成随机文件名"""
    try:
        # 只使用字母、数字和下划线，避免特殊字符
        chars = string.ascii_letters + string.digits + '_'
        return ''.join(random.choice(chars) for _ in range(length))
    except Exception as e:
        log_exception(e)
        return 'x'  # 如果生成失败，返回默认值

def is_file_in_use(file_path):
    """检查文件是否被占用"""
    try:
        # 尝试以独占模式打开文件
        with open(file_path, 'a'):
            return False
    except IOError:
        return True
    except Exception as e:
        log_exception(e)
        return True

def get_processes_using_file(file_path):
    """获取占用文件的进程列表"""
    try:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'open_files']):
            try:
                for file in proc.open_files():
                    if file.path.lower() == file_path.lower():
                        processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return processes
    except Exception as e:
        log_exception(e)
        return []

def terminate_process(process):
    """终止进程"""
    try:
        if process.is_running():
            process.terminate()
            process.wait(timeout=3)  # 等待进程终止
            if process.is_running():
                process.kill()  # 如果进程还在运行，强制结束
            return True
    except Exception as e:
        log_exception(e)
    return False

def get_all_files(directory):
    """递归获取文件夹中的所有文件"""
    try:
        files = []
        for root, _, filenames in os.walk(directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                if is_valid_file_path(file_path):
                    files.append(file_path)
        return files
    except Exception as e:
        log_exception(e)
        return []

class EraseThread(QThread):
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)
    file_status = Signal(str)
    folder_deleted = Signal(str)  # 新增信号，参数为文件夹路径

    def __init__(self, file_paths, method, force_delete=False):
        super().__init__()
        self.file_paths = [f for f in file_paths if is_valid_file_path(f)]
        self.method = method
        self.force_delete = force_delete
        self._is_running = True
        self.current_file_index = 0
        self.last_progress_update = 0
        self.update_interval = 0.5
        self.total_size = 0
        self.processed_size = 0
        if not self.file_paths:
            self.error.emit("没有有效的文件可以删除")
        logging.info(f"初始化粉碎线程: 文件数={len(self.file_paths)}, 方法={method}, 强制删除={force_delete}")

    def should_update_progress(self, current_time):
        """检查是否应该更新进度"""
        if current_time - self.last_progress_update >= self.update_interval:
            self.last_progress_update = current_time
            return True
        return False

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            total_files = len(self.file_paths)
            if total_files == 0:
                self.error.emit("没有有效的文件可以删除")
                return

            # 计算所有文件的总大小
            self.total_size = 0
            for path in self.file_paths:
                if os.path.isfile(path):
                    self.total_size += os.path.getsize(path)
                elif os.path.isdir(path):
                    for dirpath, _, filenames in os.walk(path):
                        for f in filenames:
                            fp = os.path.join(dirpath, f)
                            if os.path.exists(fp):
                                self.total_size += os.path.getsize(fp)
            
            self.processed_size = 0
            CHUNK_SIZE = 64 * 1024

            # 记录需要删除的文件夹
            folders_to_delete = set()

            # 第一遍：处理所有文件
            for idx, file_path in enumerate(self.file_paths):
                if not self._is_running:
                    logging.info("粉碎操作被用户取消")
                    self.error.emit("操作已取消")
                    return

                self.current_file_index = idx
                if not is_valid_file_path(file_path):
                    logging.warning(f"文件无效或无法访问: {file_path}")
                    continue

                # 如果是文件夹，记录它并处理其中的文件
                if os.path.isdir(file_path):
                    logging.info(f"发现文件夹: {file_path}")
                    folders_to_delete.add(file_path)
                    
                    # 处理文件夹中的所有文件
                    for root, dirs, files in os.walk(file_path, topdown=False):
                        # 先处理文件
                        for file in files:
                            full_path = os.path.join(root, file)
                            if not is_valid_file_path(full_path):
                                continue
                                
                            logging.info(f"开始处理文件夹中的文件: {full_path}")
                            self.file_status.emit(f"正在粉碎: {truncate_middle(full_path)}")
                            
                            try:
                                file_size = os.path.getsize(full_path)
                                passes = self.get_passes()
                                for i, pattern in enumerate(passes):
                                    if not self._is_running:
                                        return
                                    written = 0
                                    with open(full_path, 'r+b' if i > 0 else 'wb') as f:
                                        while written < file_size:
                                            if not self._is_running:
                                                return
                                            chunk_size = min(CHUNK_SIZE, file_size - written)
                                            chunk = self.generate_pattern(pattern, chunk_size)
                                            f.write(chunk)
                                            written += chunk_size
                                            self.processed_size += chunk_size
                                            current_time = time.time()
                                            if self.should_update_progress(current_time):
                                                total_percent = int((self.processed_size / self.total_size) * 100)
                                                self.progress.emit(total_percent)
                                    
                                    total_percent = int((self.processed_size / self.total_size) * 100)
                                    self.progress.emit(total_percent)
                                
                                try:
                                    os.remove(full_path)
                                    logging.info(f"文件已删除: {full_path}")
                                except Exception as e:
                                    logging.error(f"删除文件失败: {full_path}, 错误: {str(e)}")
                                    self.error.emit(f"删除文件失败: {truncate_middle(full_path)}")
                            except Exception as e:
                                logging.error(f"处理文件失败: {full_path}, 错误: {str(e)}")
                                self.error.emit(f"处理文件失败: {truncate_middle(full_path)}")
                        
                        # 记录子文件夹
                        for dir_name in dirs:
                            dir_path = os.path.join(root, dir_name)
                            if os.path.exists(dir_path):
                                folders_to_delete.add(dir_path)
                                logging.info(f"添加子文件夹到删除列表: {dir_path}")
                    continue

                # 处理单个文件
                logging.info(f"开始处理文件: {file_path}")
                self.file_status.emit(f"正在粉碎: {truncate_middle(file_path)}")
                
                # 检查文件是否被占用
                if is_file_in_use(file_path):
                    self.file_status.emit(f"文件 {truncate_middle(file_path)} 正在被占用，尝试终止占用进程...")
                    processes = get_processes_using_file(file_path)
                    if processes:
                        for proc in processes:
                            if terminate_process(proc):
                                logging.info(f"已终止占用文件的进程: {proc.name()} (PID: {proc.pid})")
                            else:
                                logging.warning(f"无法终止进程: {proc.name()} (PID: {proc.pid})")
                    else:
                        logging.warning(f"无法获取占用文件的进程信息: {file_path}")
                
                try:
                    file_size = os.path.getsize(file_path)
                    passes = self.get_passes()
                    for i, pattern in enumerate(passes):
                        if not self._is_running:
                            return
                        written = 0
                        with open(file_path, 'r+b' if i > 0 else 'wb') as f:
                            while written < file_size:
                                if not self._is_running:
                                    return
                                chunk_size = min(CHUNK_SIZE, file_size - written)
                                chunk = self.generate_pattern(pattern, chunk_size)
                                f.write(chunk)
                                written += chunk_size
                                self.processed_size += chunk_size
                                current_time = time.time()
                                if self.should_update_progress(current_time):
                                    total_percent = int((self.processed_size / self.total_size) * 100)
                                    self.progress.emit(total_percent)
                        total_percent = int((self.processed_size / self.total_size) * 100)
                        self.progress.emit(total_percent)
                    try:
                        os.remove(file_path)
                        logging.info(f"文件已删除: {file_path}")
                    except Exception as e:
                        logging.error(f"删除文件失败: {file_path}, 错误: {str(e)}")
                        self.error.emit(f"删除文件失败: {truncate_middle(file_path)}")
                except Exception as e:
                    logging.error(f"处理文件失败: {file_path}, 错误: {str(e)}")
                    self.error.emit(f"处理文件失败: {truncate_middle(file_path)}")

            # 第二遍：删除所有文件夹
            logging.info(f"开始处理文件夹，共 {len(folders_to_delete)} 个文件夹")
            for folder in folders_to_delete:
                try:
                    if not os.path.exists(folder):
                        continue
                        
                    logging.info(f"正在处理文件夹: {folder}")
                    self.file_status.emit(f"正在处理文件夹: {truncate_middle(folder)}")
                    
                    # 确保文件夹是空的
                    try:
                        import shutil
                        # 尝试删除空文件夹
                        os.rmdir(folder)
                        logging.info(f"空文件夹删除成功: {folder}")
                        self.folder_deleted.emit(folder)  # 发射信号
                        continue
                    except Exception as e:
                        logging.info(f"文件夹非空，尝试其他方法: {folder}")
                    
                    # 生成随机文件夹名
                    folder_dir = os.path.dirname(folder)
                    random_name = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
                    temp_folder = os.path.join(folder_dir, random_name)
                    
                    # 重命名文件夹
                    renamed = False
                    try:
                        os.rename(folder, temp_folder)
                        folder = temp_folder
                        renamed = True
                        logging.info(f"文件夹重命名成功: {folder}")
                    except Exception as rename_e:
                        logging.warning(f"重命名文件夹失败: {folder}, 错误: {str(rename_e)}")
                        try:
                            shutil.move(folder, temp_folder)
                            folder = temp_folder
                            renamed = True
                            logging.info(f"使用shutil重命名成功: {folder}")
                        except Exception as move_e:
                            logging.error(f"使用shutil重命名失败: {folder}, 错误: {str(move_e)}")
                    
                    if renamed:
                        # 尝试多种方法删除文件夹
                        try:
                            # 方法1：使用shutil.rmtree
                            shutil.rmtree(folder, ignore_errors=True)
                            if not os.path.exists(folder):
                                logging.info(f"文件夹删除成功: {folder}")
                                self.folder_deleted.emit(folder)  # 发射信号
                                continue
                        except Exception as e:
                            logging.warning(f"shutil.rmtree删除失败: {folder}")
                        
                        try:
                            # 方法2：使用系统命令
                            if os.name == 'nt':  # Windows
                                cmd = f'rmdir /s /q "{folder}"'
                            else:  # Linux/Mac
                                cmd = f'rm -rf "{folder}"'
                            
                            logging.info(f"尝试强制删除命令: {cmd}")
                            result = subprocess.run(cmd, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
                            
                            if result.returncode == 0 and not os.path.exists(folder):
                                logging.info(f"强制删除成功: {folder}")
                                self.folder_deleted.emit(folder)  # 发射信号
                            else:
                                raise Exception(f"删除失败，返回码: {result.returncode}")
                        except Exception as delete_e:
                            logging.error(f"删除文件夹失败: {folder}, 错误: {str(delete_e)}")
                            self.error.emit(f"删除文件夹失败: {truncate_middle(folder)}")
                except Exception as e:
                    logging.error(f"处理文件夹失败: {folder}, 错误: {str(e)}")
                    self.error.emit(f"处理文件夹失败: {truncate_middle(folder)}")

            if self._is_running:
                self.finished.emit()
        except Exception as e:
            error_msg = f"粉碎过程中发生错误: {str(e)}"
            logging.error(error_msg)
            self.error.emit(error_msg)

    def get_passes(self):
        if self.method == "[覆写3次] DoD 5220.22-M":
            return [
                b'\x00' * 1024,  # 第一次全0
                b'\x01' * 1024,  # 第一次全1
                b'\x00' * 1024,  # 第二次全0
                b'\x01' * 1024,  # 第二次全1
                b'\x00' * 1024,  # 第三次全0
                b'\x01' * 1024,  # 第三次全1
                bytes([random.randint(0, 255) for _ in range(1024)])  # 随机数据
            ]
        elif self.method == "[覆写7次] DoD 5220.22-M ECE":
            return [
                b'\x00' * 1024,  # 全0
                b'\xFF' * 1024,  # 全1
                bytes([random.randint(0, 255) for _ in range(1024)]),  # 随机数据
                b'\x00' * 1024,  # 全0
                b'\xFF' * 1024,  # 全1
                bytes([random.randint(0, 255) for _ in range(1024)]),  # 随机数据
                b'\x00' * 1024   # 全0
            ]
        elif self.method == "[覆写35次] Gutmann":
            # Gutmann方法的35次覆盖模式
            patterns = []
            # 前4次：全0
            for _ in range(4):
                patterns.append(b'\x00' * 1024)
            # 第5次：0x55
            patterns.append(bytes([0x55] * 1024))
            # 第6次：0xAA
            patterns.append(bytes([0xAA] * 1024))
            # 第7-10次：随机数据
            for _ in range(4):
                patterns.append(bytes([random.randint(0, 255) for _ in range(1024)]))
            # 第11-25次：特殊模式
            special_patterns = [
                0x92, 0x49, 0x24, 0x92, 0x49, 0x24, 0x92, 0x49,  # 0x9249249249249249
                0x49, 0x24, 0x92, 0x49, 0x24, 0x92, 0x49, 0x24,  # 0x4924924924924924
                0x24, 0x92, 0x49, 0x24, 0x92, 0x49, 0x24, 0x92,  # 0x2492492492492492
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # 0x0000000000000000
                0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,  # 0x1111111111111111
                0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x22,  # 0x2222222222222222
                0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x33,  # 0x3333333333333333
                0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44, 0x44,  # 0x4444444444444444
                0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55,  # 0x5555555555555555
                0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66,  # 0x6666666666666666
                0x77, 0x77, 0x77, 0x77, 0x77, 0x77, 0x77, 0x77,  # 0x7777777777777777
                0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88, 0x88,  # 0x8888888888888888
                0x99, 0x99, 0x99, 0x99, 0x99, 0x99, 0x99, 0x99,  # 0x9999999999999999
                0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA,  # 0xAAAAAAAAAAAAAAAA
                0xBB, 0xBB, 0xBB, 0xBB, 0xBB, 0xBB, 0xBB, 0xBB,  # 0xBBBBBBBBBBBBBBBB
            ]
            for pattern in special_patterns:
                patterns.append(bytes([pattern] * 1024))
            # 第26-31次：随机数据
            for _ in range(6):
                patterns.append(bytes([random.randint(0, 255) for _ in range(1024)]))
            # 最后4次：全0
            for _ in range(4):
                patterns.append(b'\x00' * 1024)
            return patterns
        elif self.method == "[覆写7次] German VSITR":
            return [
                b'\x00' * 1024,  # 全0
                b'\xFF' * 1024,  # 全1
                bytes([random.randint(0, 255) for _ in range(1024)]),  # 随机数据
                b'\x00' * 1024,  # 全0
                b'\xFF' * 1024,  # 全1
                bytes([random.randint(0, 255) for _ in range(1024)]),  # 随机数据
                b'\x00' * 1024   # 全0
            ]
        return [b'\x00' * 1024]  # 默认单次覆盖

    def generate_pattern(self, pattern, size):
        return pattern[:size]

class StyledButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(36)
        self.setCursor(Qt.PointingHandCursor)

class SplitButton(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 主按钮
        self.main_button = QPushButton(text)
        self.main_button.setMinimumHeight(36)
        self.main_button.setCursor(Qt.PointingHandCursor)
        self.main_button.setStyleSheet("""
            QPushButton {
                background-color: #3498DB;
                color: white;
                border: none;
                border-radius: 8px 0 0 8px;
                padding: 12px 24px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2980B9;
            }
            QPushButton:disabled {
                background-color: #BDC3C7;
            }
        """)
        
        # 下拉按钮
        self.dropdown_button = QPushButton("▼")
        self.dropdown_button.setFixedWidth(36)
        self.dropdown_button.setCursor(Qt.PointingHandCursor)
        self.dropdown_button.setStyleSheet("""
            QPushButton {
                background-color: #3498DB;
                color: white;
                border: none;
                border-radius: 0 8px 8px 0;
                padding: 12px 0;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2980B9;
            }
            QPushButton:disabled {
                background-color: #BDC3C7;
            }
        """)
        
        layout.addWidget(self.main_button)
        layout.addWidget(self.dropdown_button)
        
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_confirm_style(self):
        """设置确认样式（红色）"""
        style = """
            QPushButton {
                background-color: #E74C3C;
                color: white;
                border: none;
                border-radius: 8px 0 0 8px;
                padding: 12px 24px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #C0392B;
            }
            QPushButton:disabled {
                background-color: #BDC3C7;
            }
        """
        self.main_button.setStyleSheet(style)
        self.dropdown_button.setStyleSheet(style.replace("border-radius: 8px 0 0 8px", "border-radius: 0 8px 8px 0"))

    def set_normal_style(self):
        """设置普通样式（蓝色）"""
        style = """
            QPushButton {
                background-color: #3498DB;
                color: white;
                border: none;
                border-radius: 8px 0 0 8px;
                padding: 12px 24px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2980B9;
            }
            QPushButton:disabled {
                background-color: #BDC3C7;
            }
        """
        self.main_button.setStyleSheet(style)
        self.dropdown_button.setStyleSheet(style.replace("border-radius: 8px 0 0 8px", "border-radius: 0 8px 8px 0"))

    def set_progress(self, current, total, percent):
        """设置进度显示"""
        self.main_button.setText(f"正在粉碎 ({current}/{total}) {percent}%")
        self.main_button.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3498DB, stop:{percent/100} #2ecc71);
                color: white;
                border: none;
                border-radius: 8px 0 0 8px;
                padding: 12px 24px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2980B9, stop:{percent/100} #27ae60);
            }}
            QPushButton:disabled {{
                background: #BDC3C7;
            }}
        """)

class FileListItem(QWidget):
    def __init__(self, file_path, show_size, remove_callback, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        def truncate_middle(text, max_length=40):
            if len(text) <= max_length:
                return text
            part_length = (max_length - 3) // 2
            return text[:part_length] + '...' + text[-part_length:]
        name = os.path.basename(file_path)
        truncated_name = truncate_middle(name)
        # 添加文件夹图标和大小显示
        if os.path.isdir(file_path):
            if show_size:
                try:
                    total_size = 0
                    for dirpath, _, filenames in os.walk(file_path):
                        for f in filenames:
                            fp = os.path.join(dirpath, f)
                            if os.path.exists(fp):
                                total_size += os.path.getsize(fp)
                    label = QLabel(f"📁 {truncated_name}  ({MainWindow.human_size_static(total_size)})")
                except Exception:
                    label = QLabel(f"📁 {truncated_name}")
            else:
                label = QLabel(f"📁 {truncated_name}")
        else:
            if show_size:
                size = os.path.getsize(file_path)
                label = QLabel(f"📄 {truncated_name}  ({MainWindow.human_size_static(size)})")
            else:
                label = QLabel(f"📄 {truncated_name}")
        label.setStyleSheet("font-size:13px;color:#666;line-height:18px;")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        self.status_btn = QPushButton("×")
        self.status_btn.setCursor(Qt.PointingHandCursor)
        self.status_btn.setFixedSize(20, 20)
        self.status_btn.setStyleSheet('''
            QPushButton {
                background: transparent;
                color: #999;
                border: none;
                font-size: 18px;
                font-weight: bold;
                border-radius: 10px;
                padding: 0px;
                margin: 0px;
            }
            QPushButton:hover {
                background: #F5F5F5;
                color: #e74c3c;
            }
        ''')
        self.status_btn.clicked.connect(lambda: remove_callback(file_path))
        layout.addWidget(self.status_btn)
        self.setMinimumHeight(44)  # 增大最小高度，防止内容被裁剪
        self.setLayout(layout)

    def set_completed(self):
        """设置完成状态"""
        self.status_btn.setText("✓")
        self.status_btn.setStyleSheet('''
            QPushButton {
                background: transparent;
                color: #2ecc71;
                border: none;
                font-size: 18px;
                font-weight: bold;
                border-radius: 10px;
                padding: 0px;
                margin: 0px;
            }
        ''')
        self.status_btn.setEnabled(False)

    def on_remove(self):
        if not self.is_completed:
            self.remove_callback(self.file_path)

class Particle:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.vx = random.uniform(-2, 2)
        self.vy = random.uniform(-4, -1)
        self.alpha = 255
        self.size = random.uniform(2, 4)
        self.color = QColor(0, 122, 255, self.alpha)

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vy += 0.1  # 重力
        self.alpha -= 5
        self.color.setAlpha(max(0, self.alpha))
        return self.alpha > 0

class ShredAnimation(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.particles = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_particles)
        self.setFixedSize(200, 200)
        self.hide()

    def start_animation(self):
        self.particles = []
        for _ in range(50):
            self.particles.append(Particle(100, 100))
        self.timer.start(16)  # 约60fps
        self.show()

    def update_particles(self):
        self.particles = [p for p in self.particles if p.update()]
        if not self.particles:
            self.timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for particle in self.particles:
            painter.setPen(QPen(particle.color, particle.size))
            painter.drawPoint(int(particle.x), int(particle.y))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        try:
            self.setWindowTitle("QErase 1.0.0")
            self.resize(420, 360)
            
            # 添加确认状态变量
            self.confirm_erase = False
            
            # 设置窗口标志，添加拖放支持
            self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
            self.setAcceptDrops(True)  # 启用窗口级别的拖放
            
            # 设置窗口图标
            try:
                icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
                if os.path.exists(icon_path):
                    self.setWindowIcon(QIcon(icon_path))
            except Exception as e:
                logging.warning(f"设置窗口图标失败: {str(e)}")
            
            # 加载设置
            self.settings = QSettings("QErase", "Settings")
            self.show_file_size = self.settings.value("show_file_size", True, type=bool)
            self.auto_clear = self.settings.value("auto_clear", True, type=bool)
            self.show_progress_percent = self.settings.value("show_progress_percent", False, type=bool)
            
            # 创建动画部件
            self.shred_animation = ShredAnimation(self)
            self.shred_animation.move(110, 100)
            
            # 设置粉碎标准
            self.standards = ["[覆写1次] 简单覆盖", "[覆写3次] DoD 5220.22-M", "[覆写7次] DoD 5220.22-M ECE", "[覆写7次] German VSITR", "[覆写35次] Gutmann"]
            self.current_standard = self.standards[1]  # 默认使用[覆写3次] DoD 5220.22-M
            
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #FAFAFA;
                }
                QLabel {
                    color: #2C3E50;
                    font-size: 13px;
                }
                QPushButton {
                    background-color: #3498DB;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-size: 14px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background-color: #2980B9;
                }
                QPushButton:disabled {
                    background-color: #BDC3C7;
                }
                QProgressBar {
                    border: none;
                    border-radius: 4px;
                    text-align: center;
                    background-color: #ECF0F1;
                    height: 6px;
                }
                QProgressBar::chunk {
                    background-color: #3498DB;
                    border-radius: 3px;
                }
                QListWidget {
                    background: white;
                    border: 2px solid #E0E0E0;
                    border-radius: 12px;
                    font-size: 13px;
                    color: #2C3E50;
                    padding: 4px;
                }
                QListWidget::item {
                    padding: 4px 8px;
                    margin: 2px;
                    border-radius: 6px;
                }
                QListWidget::item:hover {
                    background: #F5F6FA;
                }
                QListWidget::item:selected {
                    background: #EBF5FB;
                    color: #3498DB;
                }
                QScrollArea {
                    border: none;
                    background: transparent;
                }
                QScrollBar:vertical {
                    border: none;
                    background: #F5F5F5;
                    width: 8px;
                    margin: 0px;
                }
                QScrollBar::handle:vertical {
                    background: #BDC3C7;
                    border-radius: 4px;
                    min-height: 20px;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                    height: 0px;
                }
                QMenu {
                    background-color: white;
                    border: 1px solid #E0E0E0;
                    border-radius: 8px;
                    padding: 6px;
                }
                QMenu::item {
                    padding: 8px 28px 8px 14px;
                    border-radius: 6px;
                }
                QMenu::item:selected {
                    background-color: #F5F6FA;
                }
                QAction {
                    padding: 8px 28px 8px 14px;
                }
                QAction:checked {
                    background-color: #EBF5FB;
                    color: #3498DB;
                }
            """)
            
            # 创建主窗口部件
            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            main_layout = QVBoxLayout(central_widget)
            main_layout.setContentsMargins(20, 20, 20, 20)
            main_layout.setSpacing(12)
            
            # 标题
            title_label = QLabel("QErase 文件粉碎")
            title_label.setStyleSheet("""
                font-size: 24px;
                font-weight: bold;
                color: #2C3E50;
            """)
            title_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            main_layout.addWidget(title_label)
            
            # 文件列表区域（使用QScrollArea包装）
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            
            self.file_list = QListWidget()
            self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
            self.file_list.setAcceptDrops(True)  # 允许拖放
            self.file_list.setDragDropMode(QListWidget.DropOnly)  # 只允许拖入
            self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.file_list.customContextMenuRequested.connect(self.show_context_menu)
            self.file_list.setVisible(True)
            self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.file_list.setStyleSheet("""
                QListWidget {
                    background: white;
                    border: 2px dashed #BDC3C7;
                    border-radius: 12px;
                    font-size: 13px;
                    color: #2C3E50;
                    padding: 8px;
                }
                QListWidget::item {
                    padding: 4px 8px;
                    margin: 2px;
                    border-radius: 6px;
                }
                QListWidget::item:hover {
                    background: #F5F6FA;
                }
                QListWidget::item:selected {
                    background: #EBF5FB;
                    color: #3498DB;
                }
            """)
            
            scroll_area.setWidget(self.file_list)
            main_layout.addWidget(scroll_area)
            
            # 状态标签
            self.status_label = QLabel()
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #666666;
                    font-size: 13px;
                    padding: 8px;
                    background: #FAFAFA;
                    border-radius: 8px;
                    margin-bottom: 8px;
                }
            """)
            self.status_label.setAlignment(Qt.AlignCenter)
            self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.status_label.setMinimumHeight(36)
            main_layout.addWidget(self.status_label)
            
            # 开始按钮
            self.start_button = SplitButton(f"开始粉碎 ({self.current_standard})")
            self.start_button.main_button.setEnabled(False)
            self.start_button.main_button.clicked.connect(self.start_erase)
            self.start_button.dropdown_button.clicked.connect(self.show_standard_menu)
            main_layout.addWidget(self.start_button)
            
            # 进度条
            self.progress_bar = QProgressBar()
            self.progress_bar.setVisible(False)
            self.progress_bar.setTextVisible(self.show_progress_percent)
            self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            main_layout.addWidget(self.progress_bar)
            
            # 版权信息
            copyright_label = QLabel("Copyright © 2025 QwejayHuang. All rights reserved.")
            copyright_label.setAlignment(Qt.AlignCenter)
            copyright_label.setStyleSheet("""
                color: #95A5A6;
                font-size: 12px;
                margin-top: 8px;
            """)
            copyright_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            main_layout.addWidget(copyright_label)
            
            self.selected_files = []
            self.thread = None
            self._is_closing = False
            self.folder_item_map = {}  # 路径到FileListItem的映射

            self.update_file_list()

            self.settings_button = QPushButton("⚙", self)
            self.settings_button.setFixedSize(36, 36)
            self.settings_button.setStyleSheet("""
                QPushButton {
                    background-color: white;
                    color: #7F8C8D;
                    border: 2px solid #E0E0E0;
                    border-radius: 8px;
                    font-size: 18px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #F5F6FA;
                    border-color: #BDC3C7;
                    color: #2C3E50;
                }
            """)
            self.settings_button.clicked.connect(self.show_settings_menu)
            self.settings_button.setParent(self)
            self.settings_button.raise_() 
            self.settings_button.move(self.width() - 36 - 16, 16)
            self.settings_button.show()
            self.settings_button.setFocusPolicy(Qt.NoFocus)
            self.resizeEvent = self._on_resize_with_settings_btn

        except Exception as e:
            log_exception(e)
            QMessageBox.critical(self, "错误", f"程序初始化失败: {str(e)}")
            sys.exit(1)

    def _on_resize_with_settings_btn(self, event):
        self.settings_button.move(self.width() - self.settings_button.width() - 16, 16)
        QMainWindow.resizeEvent(self, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()  # 使用 acceptProposedAction 替代 accept
        else:
            event.ignore()

    def dropEvent(self, event):
        try:
            files = []
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if os.path.isfile(path) or os.path.isdir(path):
                    files.append(path)
            if files:
                self.add_files(files)
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception as e:
            logging.error(f"处理拖放文件时出错: {str(e)}")
            event.ignore()

    def select_file(self, event=None):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.ExistingFiles)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        if dialog.exec():
            files = []
            for path in dialog.selectedFiles():
                if os.path.isfile(path) or os.path.isdir(path):
                    files.append(path)
            if files:
                self.add_files(files)

    def add_files(self, files):
        added = False
        invalid_files = []
        for file_path in files:
            if not is_valid_file_path(file_path):
                invalid_files.append(os.path.basename(file_path))
                continue
            if file_path not in self.selected_files:
                self.selected_files.append(file_path)
                added = True
        
        if invalid_files:
            self.set_status(f"以下文件无效或无法访问: {', '.join(invalid_files)}", "error")
        
        if added:
            self.start_button.main_button.setEnabled(True)
            self.set_status(f"已添加 {len(files) - len(invalid_files)} 个文件", "success")
            self.update_file_list()
        elif not invalid_files:
            self.set_status("文件已在列表中", "info")

    def show_context_menu(self, position):
        menu = QMenu()
        add_action = menu.addAction("添加文件")
        clear_action = menu.addAction("清空列表")
        action = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if action == add_action:
            self.select_file()
        elif action == clear_action:
            self.clear_file_list()

    def show_standard_menu(self):
        menu = QMenu()
        for standard in self.standards:
            action = menu.addAction(standard)
            action.setCheckable(True)
            action.setChecked(standard == self.current_standard)
        
        action = menu.exec(self.start_button.dropdown_button.mapToGlobal(self.start_button.dropdown_button.rect().bottomLeft()))
        if action:
            self.current_standard = action.text()
            self.start_button.main_button.setText(f"开始粉碎 ({self.current_standard})")

    def show_settings_menu(self):
        menu = QMenu()
        
        # 显示文件大小选项
        show_size_action = QAction("显示文件大小", menu)
        show_size_action.setCheckable(True)
        show_size_action.setChecked(self.show_file_size)
        show_size_action.triggered.connect(self.toggle_show_file_size)
        menu.addAction(show_size_action)
        
        # 自动清空列表选项
        auto_clear_action = QAction("粉碎后自动清空", menu)
        auto_clear_action.setCheckable(True)
        auto_clear_action.setChecked(self.auto_clear)
        auto_clear_action.triggered.connect(self.toggle_auto_clear)
        menu.addAction(auto_clear_action)
        
        # 显示进度百分比选项
        show_percent_action = QAction("显示进度百分比", menu)
        show_percent_action.setCheckable(True)
        show_percent_action.setChecked(self.show_progress_percent)
        show_percent_action.triggered.connect(self.toggle_show_progress_percent)
        menu.addAction(show_percent_action)
        
        menu.exec(self.settings_button.mapToGlobal(self.settings_button.rect().bottomLeft()))

    def toggle_show_file_size(self, checked):
        self.show_file_size = checked
        self.settings.setValue("show_file_size", checked)
        self.update_file_list()

    def toggle_auto_clear(self, checked):
        self.auto_clear = checked
        self.settings.setValue("auto_clear", checked)

    def toggle_show_progress_percent(self, checked):
        self.show_progress_percent = checked
        self.settings.setValue("show_progress_percent", checked)
        self.progress_bar.setTextVisible(checked)

    def update_file_list(self):
        self.file_list.clear()
        self.folder_item_map.clear()
        for file_path in self.selected_files:
            item_widget = FileListItem(file_path, self.show_file_size, self.remove_file)
            list_item = QListWidgetItem(self.file_list)
            list_item.setSizeHint(item_widget.sizeHint())  # 用控件自适应高度
            self.file_list.addItem(list_item)
            self.file_list.setItemWidget(list_item, item_widget)
            if os.path.isdir(file_path):
                self.folder_item_map[file_path] = item_widget

    def start_erase(self):
        try:
            if not self.selected_files:
                return
            
            if not self.confirm_erase:
                # 第一次点击，显示警告并设置确认状态
                self.confirm_erase = True
                self.start_button.main_button.setText("再次点击确认粉碎")
                self.start_button.set_confirm_style()
                self.set_status("数据删除后将无法恢复，请再次点击确认！", "warning")
                return
            
            # 第二次点击，开始粉碎
            self.confirm_erase = False
            self.start_button.main_button.setText("正在粉碎 (0/0) 0%")
            self.start_button.set_normal_style()
            self.start_button.main_button.setEnabled(False)
            self.set_status("正在粉碎文件...", "info")
            self.thread = EraseThread(self.selected_files, self.current_standard)
            self.thread.progress.connect(self.update_progress)
            self.thread.finished.connect(self.erase_finished)
            self.thread.error.connect(self.show_error)
            self.thread.file_status.connect(self.set_status)
            self.thread.folder_deleted.connect(self.on_folder_deleted)
            self.thread.start()
        except Exception as e:
            log_exception(e)
            self.show_error(f"启动粉碎过程失败: {str(e)}")

    def update_progress(self, value):
        """更新进度显示"""
        if hasattr(self, 'thread') and self.thread:
            current_file = self.thread.current_file_index + 1
            total_files = len(self.thread.file_paths)
            self.start_button.set_progress(current_file, total_files, value)
            # 更新当前文件的完成状态
            if value == 100 and current_file > 0:
                item = self.file_list.item(current_file - 1)
                if item:
                    widget = self.file_list.itemWidget(item)
                    if widget:
                        widget.set_completed()

    def erase_finished(self):
        if self._is_closing:
            return
        if self.auto_clear:
            self.clear_file_list()
            self.start_button.main_button.setEnabled(False)
        else:
            self.start_button.main_button.setEnabled(True)
        self.set_status("所有文件已安全删除", "success")
        self.shred_animation.start_animation()
        self.start_button.main_button.setText(f"开始粉碎 ({self.current_standard})")
        self.start_button.set_normal_style()

    def show_error(self, error_msg):
        if self._is_closing:
            return
        self.set_status(f"错误: {error_msg}", "error")
        self.start_button.main_button.setEnabled(True)

    def set_status(self, msg, status_type="info"):
        """设置状态信息，显示在状态标签中"""
        color = {
            "info": "#666666",
            "success": "#28a745",
            "error": "#dc3545",
            "warning": "#ffc107"
        }.get(status_type, "#666666")
        
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 13px;
                padding: 8px;
                background: #FAFAFA;
                border-radius: 8px;
                margin-bottom: 8px;
            }}
        """)
        self.status_label.setText(msg)
        
        if status_type in ["error", "success"]:
            QTimer.singleShot(3000, lambda: self.set_status("数据删除后无法恢复，请谨慎操作！", "warning"))

    def remove_file(self, file_path):
        if file_path in self.selected_files:
            self.selected_files.remove(file_path)
            self.update_file_list()
            if not self.selected_files:
                self.start_button.main_button.setEnabled(False)
                self.set_status("已移除所有文件", "info")
            else:
                self.set_status(f"已移除文件: {os.path.basename(file_path)}", "info")

    def clear_file_list(self):
        self.selected_files = []
        self.file_list.clear()
        self.start_button.main_button.setEnabled(False)
        self.set_status("已清空文件列表", "info")
        self.show_empty_hint()

    def show_empty_hint(self):
        self.file_list.clear()
        hint_item = QListWidgetItem("拖放文件到此区域，或右键添加文件")
        hint_item.setFlags(Qt.NoItemFlags)
        hint_item.setForeground(QColor("#b0b0b0"))
        hint_item.setTextAlignment(Qt.AlignCenter)
        self.file_list.addItem(hint_item)
        self.set_status("数据删除后无法恢复，请谨慎操作！", "warning")

    @staticmethod
    def human_size_static(size, decimal_places=2):
        for unit in ['B','KB','MB','GB','TB']:
            if size < 1024.0:
                return f"{size:.{decimal_places}f} {unit}"
            size /= 1024.0
        return f"{size:.{decimal_places}f} PB"

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(self, "确认退出", 
                "文件粉碎正在进行中，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            
            if reply == QMessageBox.Yes:
                self._is_closing = True
                self.thread.stop()
                self.thread.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def on_folder_deleted(self, folder_path):
        # 文件夹真正被删除后，界面才打勾
        item = self.folder_item_map.get(folder_path)
        if item:
            item.set_completed()

def truncate_middle(text, max_length=40):
    if len(text) <= max_length:
        return text
    part_length = (max_length - 3) // 2
    return text[:part_length] + '...' + text[-part_length:]

if __name__ == '__main__':
    try:
        setup_logging()
        logging.info("程序启动")
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        log_exception(e)
        QMessageBox.critical(None, "错误", f"程序启动失败: {str(e)}")
        sys.exit(1) 