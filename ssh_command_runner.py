"""
This script allows users to run commands on multiple devices under test (DUTs) via SSH.
It supports configuration files in YAML format, logging of command outputs and errors,
and parallel execution of commands on multiple devices. The script also provides options
to initialize a default configuration, print logs of previous command executions, and
specify a configuration file for the current run.

Usage:
    --init             Initialize the configuration directory with default values.
    --log <config>     Print the logs for the specified configuration file.
    --cfg <config>     Specify the configuration file (without .yaml extension).

Example:
    python ssh_command_runner.py --cfg my_config
    python ssh_command_runner.py --log my_config

Directory structure:
    Configurations: ~/.pytoys/configs/
    Logs: ~/.pytoys/logs/
"""

import argparse
import os
import sys
import termios
import time
import tty
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import paramiko
import yaml
from tqdm import tqdm

CONFIG_DIR = os.path.expanduser("~/.pytoys/configs/")
LOG_DIR = os.path.expanduser("~/.pytoys/logs/")
DEFAULT_TIMEOUT = 120


def ensure_directories_exist():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)


def log_with_timestamp(log_file, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file.write(f"[{timestamp}] {message}\n")


def execute_ssh_command_with_timeout(ssh, command, timeout=DEFAULT_TIMEOUT):
    stdin, stdout, stderr = ssh.exec_command(command)

    output = ""
    error = ""

    start_time = time.time()
    while True:
        if stdout.channel.exit_status_ready():
            output = stdout.read().decode()
            error = stderr.read().decode()
            break
        if time.time() - start_time > timeout:
            stdin.channel.close()
            stdout.channel.close()
            stderr.channel.close()
            raise TimeoutError(f"Command '{command}' timed out after {timeout} seconds")
        time.sleep(1)

    return output, error


def connect_ssh(hostname, username, password, port=22):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(hostname, port, username, password, timeout=10)
        return ssh
    except paramiko.AuthenticationException:
        print(f"Authentication failed when connecting to {hostname}")
    except paramiko.SSHException as sshException:
        print(f"Could not establish SSH connection: {sshException}")
    except Exception as e:
        print(f"Exception in connecting to {hostname}: {e}")
    return None


def run_commands_on_device(device, commands, log_dir, config_name, progress_bar):
    hostname = device["hostname"]
    port = device.get("port", 22)
    username = device["username"]
    password = device["password"]
    log_file_path = os.path.join(log_dir, f"{config_name}_{hostname}.log")

    with open(log_file_path, "w") as log_file:
        ssh = connect_ssh(hostname, username, password, port)
        if not ssh:
            log_with_timestamp(log_file, f"Failed to connect to {hostname}")
            progress_bar.update(len(commands))  # Update the progress bar
            return

        for cmd in commands:
            command = cmd.get("command")
            timeout = cmd.get("timeout", DEFAULT_TIMEOUT)
            sleep_time = cmd.get("sleep", 0)

            if command:
                try:
                    log_with_timestamp(log_file, f"Executing command:\n{command}")
                    output, error = execute_ssh_command_with_timeout(
                        ssh, command, timeout
                    )
                    if error:
                        log_with_timestamp(log_file, f"Error on {hostname}:\n{error}")
                    else:
                        log_with_timestamp(log_file, f"Output on {hostname}:\n{output}")
                except TimeoutError as e:
                    log_with_timestamp(log_file, f"Timeout on {hostname}:\n{e}")
                    ssh.close()
                    log_with_timestamp(log_file, f"Reconnecting to {hostname}...")
                    ssh = connect_ssh(hostname, username, password, port)
                    if not ssh:
                        log_with_timestamp(
                            log_file, f"Failed to reconnect to {hostname}"
                        )
                        break
            if sleep_time > 0:
                log_with_timestamp(log_file, f"Sleeping for {sleep_time} seconds")
                time.sleep(sleep_time)

            progress_bar.update(1)

        if ssh:
            ssh.close()


def load_config(config_file):
    with open(config_file, "r") as file:
        config = yaml.safe_load(file)
    return config


def init_config(config_dir):
    default_config = {
        "devices": [
            {
                "hostname": "192.168.0.1",
                "port": "22",
                "username": "admin",
                "password": "admin",
            }
        ],
        "commands": [
            {"command": "ls", "timeout": 10},
            {"sleep": 5},
            {"command": "pwd"},
        ],
    }

    if not os.path.exists(config_dir):
        os.makedirs(config_dir)

    config_file = os.path.join(config_dir, "default_config.yaml")
    with open(config_file, "w") as file:
        yaml.dump(default_config, file)
    print(f"Default configuration initialized at {config_file}")


def print_config(config):
    print("\nDevices:")
    for device in config["devices"]:
        print(f"  - Hostname: {device['hostname']}")
        print(f"    Port: {device.get('port', 22)}")
        print(f"    Username: {device['username']}")
        print(f"    Password: {device['password']}\n")

    print("Commands:")
    for cmd in config["commands"]:
        command = cmd.get("command", "sleep")
        timeout = cmd.get("timeout", DEFAULT_TIMEOUT)
        sleep_time = cmd.get("sleep", 0)
        if command != "sleep":
            print(f"  - Command: {command}")
            print(f"    Timeout: {timeout} seconds\n")
        if sleep_time > 0:
            print(f"  - Sleep: {sleep_time} seconds\n")


def print_log(config_name):
    config_file = os.path.join(CONFIG_DIR, config_name + ".yaml")
    if not os.path.isfile(config_file):
        print(f"Configuration file '{config_file}' does not exist.")
        return
    config = load_config(config_file)

    hostnames = [device["hostname"] for device in config["devices"]]

    log_files = [f for f in os.listdir(LOG_DIR) if f.startswith(config_name)]

    if not log_files:
        print(f"No log files found for configuration '{config_name}'.")
        return

    matched_log_files = [f for f in log_files if any(n in f for n in hostnames)]

    for log_file in matched_log_files:
        log_file_path = os.path.join(LOG_DIR, log_file)
        print(f"\nLog file: {log_file_path}\n")
        with open(log_file_path, "r") as file:
            print(file.read())


def wait_for_user_input():
    print(
        "Please review the above configuration and press Enter to continue, or press ESC/Ctrl+C to cancel.\n"
    )
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\n":
                break
            elif ch in ["\x1b", "\x03"]:  # ESC or Ctrl+C
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main():
    parser = argparse.ArgumentParser(
        description="Run commands on multiple DUTs via SSH."
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize the configuration directory with default values",
    )
    parser.add_argument(
        "--log",
        type=str,
        help="Print the logs for the specified configuration file",
    )
    parser.add_argument(
        "--cfg",
        type=str,
        help="Specify the configuration file (without .yaml extension)",
    )
    args = parser.parse_args()

    ensure_directories_exist()

    if args.init:
        init_config(CONFIG_DIR)

    elif args.log:
        print_log(args.log)

    elif args.cfg:
        config_file = os.path.join(CONFIG_DIR, args.cfg + ".yaml")
        if not os.path.isfile(config_file):
            print(f"Configuration file '{config_file}' does not exist.")
            return
        config = load_config(config_file)
        print_config(config)

        try:
            wait_for_user_input()
        except KeyboardInterrupt:
            print("Exiting...")
            return

        commands = config["commands"]
        devices = config["devices"]
        log_dir = LOG_DIR

        config_name = args.cfg

        total_tasks = len(devices) * len(commands)
        with tqdm(
            total=total_tasks, desc="Executing commands", unit="task"
        ) as progress_bar:
            with ThreadPoolExecutor(max_workers=len(devices)) as executor:
                futures = {
                    executor.submit(
                        run_commands_on_device,
                        device,
                        commands,
                        log_dir,
                        config_name,
                        progress_bar,
                    ): device
                    for device in devices
                }
                for future in as_completed(futures):
                    device = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        with open(
                            os.path.join(
                                log_dir, f"{config_name}_{device['hostname']}.log"
                            ),
                            "a",
                        ) as log_file:
                            log_with_timestamp(
                                log_file, f"Error on {device['hostname']}: {e}"
                            )
                            progress_bar.update(len(commands))

    else:
        print("Please specify a configuration file using --cfg")


if __name__ == "__main__":
    main()
