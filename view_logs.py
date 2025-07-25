import sqlite3
import os
import pandas as pd

# --- Configuration ---
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, 'progress.db')

def view_gym_audit_logs():
    """Connects to the database and prints the content of the gym_audit_log table."""
    if not os.path.exists(db_path):
        print(f"错误：在路径 {db_path}找不到数据库文件。")
        print("请确保你已经运行过一次主机器人脚本(bot.py)来生成数据库。")
        return

    try:
        conn = sqlite3.connect(db_path)
        
        # Check if the table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gym_audit_log';")
        if cursor.fetchone() is None:
            print("错误: 'gym_audit_log' 表不存在。")
            print("请确保你已经运行了更新后的 bot.py，以便创建日志表。")
            conn.close()
            return

        # Use pandas to read and display the table for better formatting
        df = pd.read_sql_query("SELECT * FROM gym_audit_log", conn)
        
        conn.close()

        if df.empty:
            print("`gym_audit_log` 表中目前没有任何记录。")
            print("请先使用 /道馆 建造, /道馆 更新, 或 /道馆 删除 指令进行一次操作。")
        else:
            print("--- 道馆操作日志 ---")
            # Set pandas to display all content without truncation
            pd.set_option('display.max_rows', None)
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)
            pd.set_option('display.max_colwidth', None)
            print(df.to_string(index=False))
            print("--------------------")

    except sqlite3.Error as e:
        print(f"数据库错误: {e}")
    except Exception as e:
        print(f"发生未知错误: {e}")

if __name__ == "__main__":
    view_gym_audit_logs()