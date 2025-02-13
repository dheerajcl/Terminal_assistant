import subprocess
import sys
import os
import re
import yaml
import click
from collections import deque
from .llm_handler import DeepSeekLLMHandler
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.syntax import Syntax
from rich.columns import Columns
from rich.rule import Rule
from rich.markdown import Markdown
from rich.console import Group

class ErrorInterceptor:
    def __init__(self):
        self.llm_handler = DeepSeekLLMHandler()
        self.command_history = deque(maxlen=20)  # Increased history depth
        self.last_command = ""
        self.context_cache = {}

    def run_command(self, command):
        """Execute command with error interception"""
        try:
            full_cmd = ' '.join(command)
            self.last_command = full_cmd
            # Maintain full session history while respecting maxlen
            if full_cmd != self.command_history[-1] if self.command_history else True:
                self.command_history.append(full_cmd)
            
            # Execute with live terminal interaction
            result = subprocess.run(
                full_cmd,
                shell=True,
                check=False,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr
            )
            
            if result.returncode != 0:
                self.context_cache = self._get_additional_context()  # Cache context
                self._handle_error(result, self.context_cache)
            
            sys.exit(result.returncode)
            
        except Exception as e:
            print(f"\n\033[91mExecution Error: {e}\033[0m")
            sys.exit(1)

    def auto_analyze(self, command, exit_code):
        """Automatically analyze failed commands from shell hook"""
        self.last_command = command
        self.command_history.append(command)
        result = subprocess.CompletedProcess(
            args=command,
            returncode=exit_code,
            stdout='',
            stderr=self._get_native_error(command)
        )
        self._handle_error(result, self.context_cache)

    def _handle_error(self, result, context):
        """Process and analyze command errors"""
        # Get relevant files from command history
        relevant_files = self._get_relevant_files_from_history()
        
        error_context = {
            'command': self.last_command,
            'error_output': self._get_full_error_output(result),
            'cwd': os.getcwd(),
            'exit_code': result.returncode,
            'history': list(self.command_history),
            'relevant_files': relevant_files,
            **context
        }

        # Enhanced context for file operations
        parts = self.last_command.split()
        if len(parts) > 0:
            base_cmd = parts[0]
            error_context['man_excerpt'] = self._get_man_page(base_cmd)

        if os.getenv('SHELLSAGE_DEBUG'):
            print("\n\033[90m[DEBUG] Error Context:")
            print(yaml.dump(error_context, allow_unicode=True) + "\033[0m")

        print("\n\033[90m🔎 Analyzing error...\033[0m")
        solution = self.llm_handler.get_error_solution(error_context)

        if solution:
            self._show_analysis(solution, error_context)
        else:
            print("\n\033[91mError: Could not get analysis\033[0m")

    def _get_relevant_files_from_history(self):
        """Extract recently referenced files from command history"""
        files = []
        for cmd in reversed(list(self.command_history)[:-1]):  # Exclude current command
            parts = cmd.split()
            if parts and parts[0] in ['touch', 'mkdir', 'cp', 'mv', 'vim', 'nano']:
                # Get the last argument as it's typically the file
                files.append(parts[-1])
                if len(files) >= 3:  # Limit to last 3 files
                    break
        return files

    def _get_man_page(self, command):
        """Get relevant sections from man page"""
        try:
            result = subprocess.run(
                f'man {command} 2>/dev/null | col -b',
                shell=True,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                content = result.stdout
                
                # Extract relevant sections
                sections = []
                current_section = None
                for line in content.split('\n'):
                    if line.upper() in ['NAME', 'SYNOPSIS', 'DESCRIPTION']:
                        current_section = line
                        sections.append(line)
                    elif current_section and line.startswith(' '):
                        sections.append(line.strip())
                    if len(sections) > 10:  # Limit size
                        break
                        
                return '\n'.join(sections)
            return "No manual entry available"
        except Exception:
            return "Error retrieving manual page"

    def _get_full_error_output(self, result):
        """Combine stderr/stdout and sanitize"""
        error_output = ''
        if hasattr(result, 'stderr') and result.stderr:
            error_output += result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        if hasattr(result, 'stdout') and result.stdout:
            error_output += '\n' + (result.stdout if isinstance(result.stdout, str) else result.stdout.decode())
        
        return re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', error_output).strip()

    def _show_analysis(self, solution, context):
        """Display analysis with thinking process"""
        console = Console()

        # Extract thinking blocks first
        thoughts = []
        remaining = solution
        while '<think>' in remaining and '</think>' in remaining:
            think_start = remaining.find('<think>') + len('<think>')
            think_end = remaining.find('</think>')
            if think_start > -1 and think_end > -1:
                thoughts.append(remaining[think_start:think_end].strip())
                remaining = remaining[think_end + len('</think>'):]
        
        console.print("\n[bold cyan]Error Analysis[/bold cyan]")
    
        # Display thinking process if any
        if thoughts:
            console.print(Panel(
                "\n".join(f"[dim]› {thought}[/dim]" for thought in thoughts),
                title="[gold1]Cognitive Process[/]",
                border_style="gold1",
                padding=(0, 2)
            ))
        
        # Context information
        context_content = []
        if context['history']:
            context_content.append(
                Panel("\n".join(f"[dim]› {cmd}[/dim]" for cmd in context['history'][-3:]),
                      title="[grey70]Recent Commands[/]",
                      border_style="grey58")
            )
        
        if context.get('relevant_files'):
            context_content.append(
                Panel("\n".join(f"[dim]› {file}[/dim]" for file in context['relevant_files']),
                      title="[grey70]Related Files[/]",
                      border_style="grey58")
            )
        
        if context.get('man_excerpt') and "No manual entry" not in context['man_excerpt']:
            context_content.append(
                Panel(
                    Syntax(context['man_excerpt'], "man", theme="ansi_light", line_numbers=False),
                    title="[bold medium_blue]📘 MANUAL REFERENCE[/]",
                    border_style="bright_blue",
                    padding=(0, 1),
                    # subtitle=f"for {os.path.basename(context['command'].split()[0])}"
                )
            )
        
        if context_content:
            console.print(Columns(context_content, equal=True, expand=False))
        
        # Error Components
        components = {
            'cause': re.search(r'🔍 Root Cause: (.+?)(?=\n🛠️|\n📚|\n⚠️|\n🔒|$)', remaining, re.DOTALL),
            'fix': re.search(r'🛠️ Fix: (`{1,3}(.*?)`{1,3}|([^\n]+))', remaining, re.DOTALL),
            'explanation': re.search(r'📚 Technical Explanation: (.+?)(?=\n⚠️|\n🔒|$)', remaining, re.DOTALL),
            'risk': re.search(r'⚠️ Potential Risks: (.+?)(?=\n🔒|$)', remaining, re.DOTALL),
            'prevention': re.search(r'🔒 Prevention Tip: (.+?)(?=\n|$)', remaining, re.DOTALL)
        }
    
        # Main Analysis Content
        analysis_blocks = []
        if components['cause']:
            analysis_blocks.append(Markdown(f"**Root Cause**\n{components['cause'].group(1)}"))
        if components['explanation']:
            analysis_blocks.append(Markdown(f"**Technical Explanation**\n{components['explanation'].group(1)}"))
        
        if analysis_blocks:
            console.print(Panel(
                Group(*analysis_blocks),
                title="[cyan]Diagnosis[/]",
                border_style="cyan",
                padding=(0, 2)
            ))
        
        # Recommended Fix
        if components['fix']:
            fix_command = components['fix'].group(1).strip('`')
            console.print(Panel(
                Syntax(fix_command, "bash", theme="ansi_light", line_numbers=False),
                title="[bold bright_green]⚡ RECOMMENDED FIX[/]",
                border_style="bright_green",
                padding=(1, 2),
                # subtitle="Copy-paste ready solution"
            ))
        
        # Additional Information
        info_blocks = []
        if components['risk']:
            info_blocks.append(Markdown(f"**Potential Risks**\n{components['risk'].group(1)}"))
        if components['prevention']:
            info_blocks.append(Markdown(f"**Prevention Tip**\n{components['prevention'].group(1)}"))
        
        if info_blocks:
            console.print(Panel(
                Group(*info_blocks),
                title="[yellow]Additional Information[/]",
                border_style="yellow",
                padding=(0, 2)
            ))
    
    def _print_component(self, match, color, label):
        """Enhanced component display"""
        if match:
            cleaned = match.group(1).replace('\n', ' ').strip()
            print(f"{color}▸ {label}:\n   {cleaned}\033[0m")

    def _prompt_fix(self, command, relevant_files):
        """Smart fix suggestion using context"""
        clean_cmd = re.sub(r'^\s*\[.*?\]\s*', '', command).strip()
        
        # If the command contains 'filename' or similar placeholder and we have relevant files
        if ('filename' in clean_cmd.lower() or 'file' in clean_cmd.lower()) and relevant_files:
            clean_cmd = clean_cmd.replace('filename', relevant_files[0])
            clean_cmd = clean_cmd.replace('file', relevant_files[0])
            
        print(f"\n\033[95m💡 Recommended fix command:\033[0m \033[92m{clean_cmd}\033[0m")

    def _get_native_error(self, command):
        """Get error output directly from command"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True
            )
            return result.stderr.strip()
        except Exception:
            return "Command execution failed"

    def _get_additional_context(self):
        """Enhanced context gathering without tmux"""
        context = {
            'env_vars': self._get_relevant_env_vars(),
            'process_tree': self._get_process_tree(),
            'file_context': self._get_file_context(),
            'network_state': self._get_network_state()
        }
        
        # Add existing git context
        if self.last_command.startswith('git '):
            context.update(self._get_git_context())
            
        return context

    def _get_relevant_env_vars(self):
        return {
            'PATH': os.getenv('PATH', ''),
            'SHELL': os.getenv('SHELL', ''),
            'USER': os.getenv('USER', ''),
            'HOME': os.getenv('HOME', ''),
            'PWD': os.getenv('PWD', ''),
            'OLDPWD': os.getenv('OLDPWD', '')
        }

    def _get_process_tree(self):
        try:
            ps_output = subprocess.check_output(
                ['ps', '-ef', '--forest'], 
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()
            return ps_output.split('\n')[-10:]  # Last 10 processes
        except Exception:
            return []

    def _get_file_context(self):
        cwd = os.getcwd()
        try:
            return {
                'files': [f for f in os.listdir(cwd) if os.path.isfile(f)][:10],
                'dirs': [d for d in os.listdir(cwd) if os.path.isdir(d)][:5]
            }
        except Exception:
            return {}

    def _get_network_state(self):
        try:
            return subprocess.check_output(
                ['ss', '-tulpn'],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip().split('\n')[:5]
        except Exception:
            return []

    def _get_git_context(self):
        try:
            git_status = subprocess.run(
                'git status --porcelain',
                shell=True,
                capture_output=True,
                text=True
            )
            git_remotes = subprocess.run(
                'git remote -v',
                shell=True,
                capture_output=True,
                text=True
            ).stdout
            return {
                'git_status': git_status.stdout,
                'git_remotes': git_remotes
            }
        except Exception:
            return {}