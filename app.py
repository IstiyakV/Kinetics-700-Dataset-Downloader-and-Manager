import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from gui.main_window import MainWindow

def main():
    # Configure high-DPI scaling for crisp visual display on modern high-resolution screens
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    app = QApplication(sys.argv)
    
    # Apply modern font settings
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
