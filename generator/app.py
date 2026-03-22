"""
Deadlock Generator: This app can simulate any application that fetches to a database

Simulates a monolith application with a classic lock ordering
bug. Two threads update the same two tables in opposite order,
creating a deadlock that PostgreSQL detects and resolves by
killing one session.

This runs on a timer and creates a real deadlock every N seconds.
The agent's job is to detect these, diagnose them, and fix them.

Thread A: UPDATE orders → UPDATE inventory  (holds orders lock, wants inventory)
Thread B: UPDATE inventory → UPDATE orders  (holds inventory lock, wants orders)

Result: deadlock. PostgreSQL kills one. The other completes.

"""

import os
import time
import random
import threading
import logging
import psycopg2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GENERATOR] %(message)s",
)
log = logging.getLogger(__name__)

# Database connection settings (from environment)
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "demoapp"),
    "user": os.getenv("DB_USER", "app"),
    "password": os.getenv("DB_PASS", "apppass"),
}

# How often to create a deadlock (seconds)
DEADLOCK_INTERVAL = int(os.getenv("DEADLOCK_INTERVAL", "120"))


def thread_a(product_id: int, order_id: int):
    """
    Thread A: locks orders first, then inventory.
    This is the "order processing" code path.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Step 1: Lock a row in orders
        log.info(f"  Thread A: UPDATE orders SET status='processing' WHERE order_id={order_id}")
        cur.execute(
            "UPDATE orders SET status='processing', updated_at=NOW() WHERE order_id=%s",
            (order_id,),
        )

        # Small delay to ensure Thread B grabs the other lock
        time.sleep(1)

        # Step 2: Try to lock a row in inventory (BLOCKED - Thread B holds this)
        log.info(f"  Thread A: UPDATE inventory SET reserved=reserved+1 WHERE product_id={product_id}")
        cur.execute(
            "UPDATE inventory SET reserved=reserved+1, updated_at=NOW() WHERE product_id=%s",
            (product_id,),
        )

        conn.commit()
        log.info("  Thread A: committed successfully")

    except psycopg2.errors.DeadlockDetected:
        log.warning("  Thread A: DEADLOCK DETECTED - I was killed by PostgreSQL")
        conn.rollback()
    except Exception as e:
        log.error(f"  Thread A: error - {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def thread_b(product_id: int, order_id: int):
    """
    Thread B: locks inventory first, then orders.
    This is the "inventory update" code path.
    Opposite lock order from Thread A = deadlock.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Step 1: Lock a row in inventory
        log.info(f"  Thread B: UPDATE inventory SET stock=stock-1 WHERE product_id={product_id}")
        cur.execute(
            "UPDATE inventory SET stock=stock-1, updated_at=NOW() WHERE product_id=%s",
            (product_id,),
        )

        # Small delay to ensure Thread A grabs the other lock
        time.sleep(1)

        # Step 2: Try to lock a row in orders (BLOCKED - Thread A holds this)
        log.info(f"  Thread B: UPDATE orders SET status='shipped' WHERE order_id={order_id}")
        cur.execute(
            "UPDATE orders SET status='shipped', updated_at=NOW() WHERE order_id=%s",
            (order_id,),
        )

        conn.commit()
        log.info("  Thread B: committed successfully")

    except psycopg2.errors.DeadlockDetected:
        log.warning("  Thread B: DEADLOCK DETECTED - I was killed by PostgreSQL")
        conn.rollback()
    except Exception as e:
        log.error(f"  Thread B: error - {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def create_deadlock():
    """
    Create a real deadlock by launching two threads that
    lock two tables in opposite order.
    """
    # Pick a random product and order to fight over
    product_id = random.randint(1, 10)
    order_id = random.randint(1, 5)

    log.info(f"Creating deadlock: product_id={product_id}, order_id={order_id}")

    # Launch both threads simultaneously
    t_a = threading.Thread(target=thread_a, args=(product_id, order_id))
    t_b = threading.Thread(target=thread_b, args=(product_id, order_id))

    t_a.start()
    t_b.start()

    t_a.join(timeout=15)
    t_b.join(timeout=15)

    log.info("Deadlock cycle complete.\n")


def main():
    """Main loop: create deadlocks on a timer."""
    log.info("=" * 50)
    log.info("DEADLOCK GENERATOR STARTED")
    log.info(f"Interval: every {DEADLOCK_INTERVAL} seconds")
    log.info(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    log.info("=" * 50)

    # Wait for the database to be fully ready
    time.sleep(5)

    # Also create some normal traffic so it's not just deadlocks
    while True:
        # Normal transaction (no deadlock)
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("UPDATE inventory SET stock=stock+1 WHERE product_id=%s", (random.randint(1, 10),))
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass

        # Every DEADLOCK_INTERVAL seconds, create a deadlock
        create_deadlock()

        # Add some jitter so it's not perfectly periodic
        jitter = random.randint(0, 30)
        log.info(f"Next deadlock in {DEADLOCK_INTERVAL + jitter} seconds...")
        time.sleep(DEADLOCK_INTERVAL + jitter)


if __name__ == "__main__":
    main()
