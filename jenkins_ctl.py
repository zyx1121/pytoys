import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
import typer
import yaml
from pydantic import BaseModel
from requests.auth import HTTPBasicAuth
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

app = typer.Typer()
console = Console()

CONFIG_FILE_PATH = Path.home() / ".pytoys" / "config.yaml"


class JenkinsConfig(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    token: Optional[str] = None


def load_config() -> JenkinsConfig:
    if CONFIG_FILE_PATH.exists():
        with CONFIG_FILE_PATH.open("r") as file:
            data = yaml.safe_load(file)
            return JenkinsConfig(**data)
    return JenkinsConfig()


def save_config(config: JenkinsConfig):
    CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE_PATH.open("w") as file:
        yaml.safe_dump(config.dict(), file)


@app.command()
def config(key: str, value: str):
    """
    Set Jenkins configuration parameters
    Usage:
    python jenkins_ctl.py config <key> <value>

    Example:

    python jenkins_ctl.py config url "https://jenkins.clounix.com"

    python jenkins_ctl.py config username "your_username"

    python jenkins_ctl.py config token "your_token"
    """
    config = load_config()
    if key == "url":
        config.url = value
    elif key == "username":
        config.username = value
    elif key == "token":
        config.token = value
    else:
        console.print(Panel(f"Unknown configuration key: {key}", style="bold red"))
        raise typer.Exit(code=1)
    save_config(config)
    console.print(Panel(f"Set {key} to {value}", style="green", border_style="dim"))


@app.command()
def build(
    job: str = typer.Argument(..., help="Jenkins job path, e.g., sv/protocol_tests"),
    params: Optional[List[str]] = typer.Argument(
        None, help="Job parameters in key=value format"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "-f", "--file", help="YAML file containing job parameters"
    ),
):
    """
    Trigger a Jenkins job
    Usage:
    python jenkins_ctl.py build <job> <key1>=<value1> <key2>=<value2> ... -f <config_file>
    """
    config = load_config()
    if not config.url or not config.username or not config.token:
        console.print(
            Panel(
                "Please configure Jenkins URL, username, and token using the `config` command.",
                style="bold red",
            )
        )
        raise typer.Exit()

    # Parse parameters from command line
    parameters = {}
    if params:
        for param in params:
            try:
                key, value = param.split("=")
                parameters[key] = value
            except ValueError:
                console.print(
                    Panel(
                        f"Invalid parameter format: {param}. Should be key=value",
                        style="bold red",
                    )
                )
                raise typer.Exit(code=1)

    # Parse parameters from file
    if config_file:
        with config_file.open("r") as file:
            file_parameters: Dict[str, str] = yaml.safe_load(file)
            if not isinstance(file_parameters, dict):
                console.print(
                    Panel(
                        "Invalid format in the config file. It should be a dictionary.",
                        style="bold red",
                    )
                )
                raise typer.Exit(code=1)
            parameters.update(file_parameters)

    # Show parameters for confirmation
    params_text = "\n".join(
        [
            f"[cyan]{key}[/cyan] = [yellow]{value}[/yellow]"
            for key, value in parameters.items()
        ]
    )
    console.print(
        Panel(
            params_text,
            title="Parameters",
            title_align="left",
            border_style="dim",
        ),
    )

    if not Confirm.ask("\nDo you want to proceed with these parameters?"):
        console.print(Panel("Build cancelled.", style="red"))
        raise typer.Exit()

    # Construct URL
    job_path = "/job/".join(job.split("/"))
    build_url = f"{config.url}/job/{job_path}/buildWithParameters"

    # Send request to trigger Jenkins job
    try:
        response = requests.post(
            build_url,
            params=parameters,
            auth=HTTPBasicAuth(config.username, config.token),
        )
        response.raise_for_status()
        console.print(
            Panel("Build triggered successfully", style="green", border_style="dim")
        )
    except requests.RequestException as e:
        console.print(Panel(f"Build trigger failed: {e}", style="bold red"))
        raise typer.Exit(code=1)


@app.command()
def info(
    job: str = typer.Argument(..., help="Jenkins job path, e.g., sv/protocol_tests")
):
    """
    Display Jenkins job status
    Usage:
    python jenkins_ctl.py info <job>
    """
    config = load_config()
    if not config.url or not config.username or not config.token:
        console.print(
            Panel(
                "Please configure Jenkins URL, username, and token using the `config` command.",
                style="bold red",
            )
        )
        raise typer.Exit()

    # Construct URL
    job_path = "/job/".join(job.split("/"))
    info_url = f"{config.url}/job/{job_path}/lastBuild/api/json"

    # Send request to get Jenkins job status
    try:
        response = requests.get(
            info_url, auth=HTTPBasicAuth(config.username, config.token)
        )
        response.raise_for_status()
    except requests.RequestException as e:
        console.print(Panel(f"Failed to get status: {e}", style="bold red"))
        raise typer.Exit(code=1)

    build_info = response.json()
    timestamp = datetime.datetime.fromtimestamp(build_info["timestamp"] / 1000)
    duration_seconds = build_info["duration"] / 1000
    duration_hours, remainder = divmod(duration_seconds, 3600)
    duration_minutes, duration_seconds = divmod(remainder, 60)
    formatted_duration = f"{int(duration_hours):02}:{int(duration_minutes):02}:{int(duration_seconds):02}"

    status = "building" if build_info.get("building") else build_info["result"]
    info_text = (
        f"[bold][cyan]Job:[/cyan] {job}[/bold]\n"
        f"[cyan]Status:[/cyan] {status}\n"
        f"[cyan]BuildTime:[/cyan] {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"[cyan]Duration:[/cyan] {formatted_duration}"
    )
    console.print(
        Panel(info_text, title="Job Info", title_align="left", border_style="dim")
    )


if __name__ == "__main__":
    app()
