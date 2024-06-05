"""
This script allows users to run commands on multiple devices under test (DUTs) via SSH.
It supports configuration files in YAML format, logging of command outputs and errors,
and parallel execution of commands on multiple devices. The script also provides options
to initialize a default configuration, print logs of previous command executions, and
specify a configuration file for the current run.

Usage:
    init             Initialize the configuration directory with default values.
    log <config>     Print the logs for the specified configuration file.
    run <config>     Specify the configuration file (without .yaml extension).

Example:
    python ssh_command_runner.py run my_config
    python ssh_command_runner.py log my_config

Directory structure:
    Configurations: ~/.pytoys/configs/
    Logs: ~/.pytoys/logs/
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import paramiko
import typer
import yaml
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm

app = typer.Typer()
console = Console()

CONFIG_DIR = Path.home() / ".pytoys" / "configs"
LOG_DIR = Path.home() / ".pytoys" / "logs"
DEFAULT_TIMEOUT = 120


def ensure_directories_exist():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(config_name, device_name, clear_logs=False):
    log_file_path = LOG_DIR / f"{config_name}_{device_name}.log"
    logger = logging.getLogger(f"{config_name}_{device_name}")

    if not logger.handlers:  # Check if handlers are already set
        logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(log_file_path, mode="w" if clear_logs else "a")
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


def execute_ssh_command(ssh, command, logger, timeout=DEFAULT_TIMEOUT):
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


def connect_ssh(hostname, username, password, logger, port=22, retries=3):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for attempt in range(retries):
        try:
            ssh.connect(hostname, port, username, password, timeout=10)
            logger.info(f"Connected to {hostname}\n")
            return ssh
        except paramiko.AuthenticationException:
            logger.error(f"Authentication failed when connecting to {hostname}")
        except paramiko.SSHException as sshException:
            logger.error(f"Could not establish SSH connection: {sshException}")
        except Exception as e:
            logger.error(f"Exception in connecting to {hostname}: {e}")

        time.sleep(5)  # Wait before retrying

    return None


def run_commands(device, commands, config_name, task_id, progress):
    hostname = device["hostname"]
    port = device.get("port", 22)
    username = device["username"]
    password = device["password"]
    logger = setup_logger(config_name, hostname, clear_logs=True)

    ssh = connect_ssh(hostname, username, password, logger, port)
    if not ssh:
        logger.error(f"Failed to connect to {hostname}")
        progress.update(task_id, advance=len(commands))
        return

    for cmd in commands:
        command = cmd.get("command")
        timeout = cmd.get("timeout", DEFAULT_TIMEOUT)
        sleep_time = cmd.get("sleep", 0)

        if command:
            try:
                logger.info(f"Executing command:\n{command}\n")
                output, error = execute_ssh_command(ssh, command, logger, timeout)
                if error:
                    logger.error(f"Error:\n{error}")
                else:
                    logger.info(f"Output:\n{output}")
            except TimeoutError as e:
                logger.error(f"Timeout on {hostname}: {e}")
                ssh.close()
                logger.info(f"Reconnecting to {hostname}...")
                ssh = connect_ssh(hostname, username, password, logger, port)
                if not ssh:
                    logger.error(f"Failed to reconnect to {hostname}")
                    break
        if sleep_time > 0:
            logger.info(f"Sleeping for {sleep_time} seconds")
            time.sleep(sleep_time)

        progress.update(task_id, advance=1)

    if ssh:
        ssh.close()


def load_config(config_file: Path):
    with config_file.open("r") as file:
        config = yaml.safe_load(file)
    return config


def print_config(config):
    devices_text = "\n".join(
        [
            f"[cyan]Hostname:[/cyan] {device['hostname']}\n[cyan]Port:[/cyan] {device.get('port', 22)}\n[cyan]Username:[/cyan] {device['username']}\n[cyan]Password:[/cyan] {device['password']}\n"
            for device in config["devices"]
        ]
    )
    commands_text = "\n".join(
        [
            f"[cyan]Command:[/cyan] {cmd.get('command', 'sleep')}\n[cyan]Timeout:[/cyan] {cmd.get('timeout', DEFAULT_TIMEOUT)} seconds\n[cyan]Sleep:[/cyan] {cmd.get('sleep', 0)} seconds\n"
            for cmd in config["commands"]
            if "command" in cmd or "sleep" in cmd
        ]
    )
    console.print(
        Panel(
            devices_text,
            title="Configuration",
            title_align="left",
            border_style="dim",
        )
    )
    console.print(
        Panel(
            commands_text,
            title="Commands",
            title_align="left",
            border_style="dim",
        )
    )


@app.command()
def init():
    """
    Initialize the configuration directory with default values.
    """
    ensure_directories_exist()

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

    config_name = typer.prompt("Enter the configuration name", default="default_config")
    config_file = CONFIG_DIR / f"{config_name}.yaml"
    with config_file.open("w") as file:
        yaml.dump(default_config, file)
    console.print(
        Panel(
            f"Default configuration initialized at {config_file}",
            style="green",
            border_style="dim",
        )
    )


@app.command()
def log(config_name: str):
    """
    Print the logs for the specified configuration file.
    """
    ensure_directories_exist()

    config_file = CONFIG_DIR / f"{config_name}.yaml"
    if not config_file.is_file():
        console.print(
            Panel(
                f"Configuration file '{config_file}' does not exist.", style="bold red"
            )
        )
        return
    config_data = load_config(config_file)

    hostnames = [device["hostname"] for device in config_data["devices"]]

    log_files = [f for f in LOG_DIR.iterdir() if f.name.startswith(config_name)]

    if not log_files:
        console.print(
            Panel(
                f"No log files found for configuration '{config_name}'.",
                style="bold red",
            )
        )
        return

    matched_log_files = [f for f in log_files if any(n in f.name for n in hostnames)]

    for log_file in matched_log_files:
        console.print(
            Panel(
                f"{log_file}", title="Log file", title_align="left", border_style="dim"
            )
        )
        with log_file.open("r") as file:
            console.print(file.read())


@app.command()
def run(config: str):
    """
    Specify the configuration file (without .yaml extension).
    """
    ensure_directories_exist()

    config_file = CONFIG_DIR / f"{config}.yaml"
    if not config_file.is_file():
        console.print(
            Panel(
                f"Configuration file '{config_file}' does not exist.", style="bold red"
            )
        )
        return

    config_data = load_config(config_file)
    print_config(config_data)

    try:
        if not Confirm.ask("\nDo you want to proceed with these parameters?"):
            console.print(Panel("Run cancelled.", style="red"))
            raise typer.Exit()
    except KeyboardInterrupt:
        console.print(Panel("Exiting...", style="bold red"))
        return

    print()

    commands = config_data["commands"]
    devices = config_data["devices"]

    config_name = config

    total_tasks = len(devices) * len(commands)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} tasks"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task_id = progress.add_task("Executing commands", total=total_tasks)
        num_devices = len(devices)
        max_workers = min(10, num_devices)  # Set a reasonable maximum number of workers

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    run_commands,
                    device,
                    commands,
                    config_name,
                    task_id,
                    progress,
                ): device
                for device in devices
            }
            for future in as_completed(futures):
                device = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger = setup_logger(config_name, device["hostname"])
                    logger.error(f"Error on {device['hostname']}: {e}")
                    progress.update(task_id, advance=len(commands))


if __name__ == "__main__":
    app()
