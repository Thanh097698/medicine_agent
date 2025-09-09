from pathlib import Path
from typing import List, Union
import mysql.connector
from mysql.connector import errorcode, Error
from loguru import logger
import pandas as pd

# load config values (assumes your config/configs.py loads dotenv already)
from src.config.configs import MYSQL_URL, MYSQL_PORT, MYSQL_NAME, MYSQL_PASSWORD, MYSQL_DB


class MySQLConnector:
    def __init__(
        self,
        host: str = MYSQL_URL,
        port: Union[int, str] = MYSQL_PORT,
        user: str = MYSQL_NAME,
        passwd: str = MYSQL_PASSWORD,
        database: str = MYSQL_DB,
    ):
        # ensure port is int
        try:
            port = int(port)
        except Exception:
            logger.warning("MYSQL_PORT invalid, fallback to 3306")
            port = 3306

        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd
        self.database = database

        # First connect without database to create DB if not exists
        try:
            logger.debug("Connecting to MySQL server %s:%s as user=%s", host, port, user)
            self.mydb = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.passwd,
                port=self.port,
                connection_timeout=10,
                autocommit=False,
            )
        except Error as e:
            logger.error("Cannot connect to MySQL server: %s", e)
            raise

        try:
            cursor = self.mydb.cursor()
            cursor.execute("SHOW DATABASES")
            db_list = [row[0] for row in cursor.fetchall()]
            if self.database not in db_list:
                logger.info("Database '%s' not found. Creating...", self.database)
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
                )
                self.mydb.commit()
                logger.info("Database '%s' created.", self.database)
            cursor.close()
            self.mydb.close()
        except Error as e:
            logger.error("Error when checking/creating database: %s", e)
            # try to close if open
            try:
                cursor.close()
            except Exception:
                pass
            try:
                self.mydb.close()
            except Exception:
                pass
            raise

        # reconnect with database selected
        try:
            self.mydb = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.passwd,
                port=self.port,
                database=self.database,
                connection_timeout=10,
            )
            logger.success("Connected to database: %s@%s:%s/%s", self.user, self.host, self.port, self.database)
        except Error as e:
            logger.error("Cannot connect to database '%s': %s", self.database, e)
            raise

    def _get_cursor(self, buffered: bool = True):
        return self.mydb.cursor(buffered=buffered)

    def check_exists_table(self, table_name: str) -> bool:
        mycursor = self._get_cursor()
        try:
            mycursor.execute("SHOW TABLES")
            tables = mycursor.fetchall()
            return (table_name,) in tables
        finally:
            mycursor.close()

    def create_table(self, table_name: str, schema_path: Union[str, Path] = None) -> bool:
        """
        Create table from schema file located at:
        ./src/database/schema/{table_name}.txt  (default)
        The schema file should contain a valid CREATE TABLE ...; statement.
        Returns True if table created or exists, False on failure.
        """
        if schema_path is None:
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema" / f"{table_name}.txt"
        else:
            schema_path = Path(schema_path)

        if not schema_path.exists():
            logger.error("Schema file not found: %s", schema_path)
            return False

        if self.check_exists_table(table_name=table_name):
            logger.warning("Table %s already exists.", table_name)
            return True

        try:
            # read with utf-8 and fallback replace any invalid characters
            with open(schema_path, "r", encoding="utf-8", errors="replace") as f:
                create_table_query = " ".join([line.strip() for line in f.readlines() if line.strip()])

            if not create_table_query:
                logger.error("Empty create table query in %s", schema_path)
                return False

            mycursor = self._get_cursor(buffered=False)
            mycursor.execute(create_table_query)
            self.mydb.commit()
            mycursor.close()
            logger.info("Create table %s success!", table_name)
            return True
        except Error as err:
            logger.error("Error in create table: %s", err)
            try:
                mycursor.close()
            except Exception:
                pass
            return False

    def insert_to_web_pages(self, table_name: str, url: str, html: str, title: str) -> bool:
        try:
            query = f"INSERT INTO `{table_name}` (url, html, title) VALUES (%s, %s, %s)"
            mycursor = self._get_cursor(buffered=False)
            mycursor.execute(query, (url, html, title))
            self.mydb.commit()
            mycursor.close()
            return True
        except Error as err:
            logger.error("Error when insert to table %s: %s", table_name, err)
            return False

    def custom_query(self, query: str, data=None):
        """
        Executes a query and returns fetched rows if any.
        For write queries, set data and commit externally as needed.
        """
        mycursor = self._get_cursor()
        try:
            if data is not None:
                mycursor.execute(query, data)
            else:
                mycursor.execute(query)
            # try to fetch if query returns rows
            try:
                result = mycursor.fetchall()
            except Exception:
                result = None
            return result
        except Error as err:
            logger.error("Error when query to database: %s", err)
            return None
        finally:
            mycursor.close()

    def update_medicine(self, id: str, assign: str) -> bool:
        try:
            query = """
                INSERT INTO medicine_detailt (id, assign) 
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                assign = VALUES(assign);
                """
            mycursor = self._get_cursor(buffered=False)
            mycursor.execute(query, (id, assign))
            self.mydb.commit()
            mycursor.close()
            return True
        except Error as err:
            logger.error("Error when insert/update medicine_detailt: %s -- id=%s assign=%s", err, id, assign)
            return False

    def insert_to_medicine_detail(self, detail_data: tuple) -> bool:
        try:
            query = (
                "INSERT INTO medicine_detailt (name, type, specification, assign, short_description, "
                "ingredient, usesage, dosage, adverseEffect, careful, preservation, price, image_url, note, FAQ, rate, QA) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);"
            )
            mycursor = self._get_cursor(buffered=False)
            mycursor.execute(query, detail_data)
            self.mydb.commit()
            mycursor.close()
            return True
        except Error as err:
            logger.error("Error when insert to medicine_detailt: %s", err)
            logger.debug("detail_data: %s", detail_data)
            return False

    def insert_to_chunks(self, data: List[List[str]]) -> bool:
        try:
            query = "INSERT INTO chunks (text, metadata) VALUES (%s, %s)"
            mycursor = self._get_cursor(buffered=False)
            mycursor.executemany(query, data)
            self.mydb.commit()
            mycursor.close()
            return True
        except Error as err:
            logger.error("Error when insert to chunks: %s", err)
            return False

    def export_data(self, table_name: str, file_path: Union[str, Path]) -> bool:
        try:
            query = f"SELECT * FROM `{table_name}`"
            data = pd.read_sql(query, self.mydb)
            data.to_csv(file_path, index=False, encoding="utf-8-sig")
            logger.success("Saved table %s to %s", table_name, file_path)
            return True
        except Exception as err:
            logger.error("Error when export table to csv: %s", err)
            return False

    def close(self):
        try:
            if self.mydb and self.mydb.is_connected():
                self.mydb.close()
                logger.debug("MySQL connection closed.")
        except Exception as e:
            logger.warning("Error closing DB connection: %s", e)

    # ensure object cleans up on garbage collection
    def __del__(self):
        try:
            self.close()
        except Exception:
            pass