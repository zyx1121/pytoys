import datetime
from pathlib import Path
from typing import Dict, List, Optional

import jenkins
import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

app = typer.Typer()
console = Console()

CONFIG_FILE_PATH = Path.home() / ".pytoys" / "config.yaml"


def load_config() -> Dict[str, Optional[str]]:
    if CONFIG_FILE_PATH.exists():
        with CONFIG_FILE_PATH.open("r") as file:
            return yaml.safe_load(file)
    return {"url": None, "username": None, "token": None}


def save_config(config: Dict[str, Optional[str]]):
    CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE_PATH.open("w") as file:
        yaml.safe_dump(config, file)


@app.command()
def config(key: str, value: str):
    """
    Set Jenkins configuration parameters
    Usage:
    python jenkins_ctl.py config <key> <value>
    """
    config = load_config()
    if key in config:
        config[key] = value
        save_config(config)
        console.print(Panel(f"Set {key} to {value}", style="green", border_style="dim"))
    else:
        console.print(Panel(f"Unknown configuration key: {key}", style="bold red"))
        raise typer.Exit(code=1)


def validate_config(config: Dict[str, Optional[str]]):
    if not config["url"] or not config["username"] or not config["token"]:
        console.print(
            Panel(
                "Please configure Jenkins URL, username, and token using the `config` command.",
                style="bold red",
            )
        )
        raise typer.Exit()


def get_jenkins_server(config: Dict[str, Optional[str]]) -> jenkins.Jenkins:
    return jenkins.Jenkins(
        config["url"], username=config["username"], password=config["token"]
    )


def parse_params(params: Optional[List[str]]) -> Dict[str, str]:
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
    return parameters


def load_params_from_file(config_file: Optional[Path]) -> Dict[str, str]:
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
            return file_parameters
    return {}


def confirm_parameters(parameters: Dict[str, str]) -> bool:
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
    return Confirm.ask("\nDo you want to proceed with these parameters?")


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
    validate_config(config)

    parameters = parse_params(params)
    parameters.update(load_params_from_file(config_file))

    if not confirm_parameters(parameters):
        console.print(Panel("Build cancelled.", style="red"))
        raise typer.Exit()

    server = get_jenkins_server(config)

    try:
        server.build_job(job, parameters)
        console.print(
            Panel("Build triggered successfully", style="green", border_style="dim")
        )
    except jenkins.JenkinsException as e:
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
    validate_config(config)

    server = get_jenkins_server(config)

    try:
        last_build_number = server.get_job_info(job)["lastBuild"]["number"]
        build_info = server.get_build_info(job, last_build_number)
    except jenkins.JenkinsException as e:
        console.print(Panel(f"Failed to get status: {e}", style="bold red"))
        raise typer.Exit(code=1)

    timestamp = datetime.datetime.fromtimestamp(build_info["timestamp"] / 1000)
    formatted_duration = str(datetime.timedelta(milliseconds=build_info["duration"]))
    status = "building" if build_info.get("building") else build_info["result"]

    info_text = (
        f"[bold][cyan]Job:[/cyan] {job}[/bold]\n"
        f"[cyan]Status:[/cyan] {status}\n"
        f"[cyan]Build Time:[/cyan] {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"[cyan]Duration:[/cyan] {formatted_duration}"
    )
    console.print(
        Panel(info_text, title="Job Info", title_align="left", border_style="dim")
    )


if __name__ == "__main__":
    app()
