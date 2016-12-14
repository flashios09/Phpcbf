import sublime
import sublime_plugin
import sys
import subprocess
import re
import threading
import os
from os.path import expanduser
try:
    from HTMLParser import HTMLParser
except:
    from html.parser import HTMLParser
from pprint import pprint

class Pref:

    project_file = None

    keys = [
        "show_debug",
        "extensions_to_execute",
        "extensions_to_blacklist",
        "on_save",
        "executable_path",
        "additional_args",
    ]

    def load(self):
        self.settings = sublime.load_settings('phpcbf.sublime-settings')
        if sublime.active_window() is not None and sublime.active_window().active_view() is not None:
            project_settings = sublime.active_window().active_view().settings()
            if project_settings.has("Phpcbf"):
                project_settings.clear_on_change('Phpcbf')
                self.project_settings = project_settings.get('Phpcbf')
                project_settings.add_on_change('Phpcbf', pref.load)
            else:
                self.project_settings = {}
        else:
            self.project_settings = {}

        for key in self.keys:
            self.settings.clear_on_change(key)
            setattr(self, key, self.get_setting(key))
            self.settings.add_on_change(key, pref.load)

    def get_setting(self, key):
        if key in self.project_settings:
            return self.project_settings.get(key)
        else:
            return self.settings.get(key)

    def set_setting(self, key, value):
        if key in self.project_settings:
            self.project_settings[key] = value
        else:
            self.settings.set(key, value)


pref = Pref()

st_version = 2
if sublime.version() == '' or int(sublime.version()) > 3000:
    st_version = 3

if st_version == 2:
    pref.load()


def plugin_loaded():
    pref.load()


def debug_message(msg):
    if pref.show_debug is True:
        print("[Phpcbf] " + str(msg))


class CheckstyleError():
    """Represents an error that needs to be displayed on the UI for the user"""
    def __init__(self, line, message):
        self.line = line
        self.message = message

    def get_line(self):
        return self.line

    def get_message(self):
        data = self.message

        if st_version == 3:
            return HTMLParser().unescape(data)
        else:
            try:
                data = data.decode('utf-8')
            except UnicodeDecodeError:
                data = data.decode(sublime.active_window().active_view().settings().get('fallback_encoding'))
            return HTMLParser().unescape(data)

    def set_point(self, point):
        self.point = point

    def get_point(self):
        return self.point


class ShellCommand():
    """Base class for shelling out a command to the terminal"""
    def __init__(self):
        self.error_list = []

        # Default the working directory for the shell command to the user's home dir.
        self.workingDir = expanduser("~")

    def setWorkingDir(self, dir):
        self.workingDir = dir

    def get_errors(self, path):
        self.execute(path)
        return self.error_list

    def shell_out(self, cmd):
        data = None

        if st_version == 3:
            debug_message(' '.join(cmd))
        else:
            for index, arg in enumerate(cmd[:]):
                cmd[index] = arg.encode(sys.getfilesystemencoding())

            debug_message(' '.join(cmd))

        debug_message(' '.join(cmd))

        info = None
        if os.name == 'nt':
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = subprocess.SW_HIDE

        debug_message("cwd: " + self.workingDir)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            startupinfo=info,
            cwd=self.workingDir
            )

        if proc.stdout:
            data = proc.communicate()[0]

        if st_version == 3:
            return data.decode()
        else:
            return data

    def execute(self, path):
        debug_message('Command not implemented')


class CodeBeautifier(ShellCommand):
    """Concrete class for phpcbf"""
    def execute(self, path):

        args = []

        if pref.executable_path != "":
            if (len(args) > 0):
                args.append(pref.executable_path)
            else:
                args = [pref.executable_path]
        else:
            debug_message("Phpcbf.executable_path is not set, therefore cannot execute")
            sublime.error_message('The "Phpcbf.executable_path" is not set, therefore cannot execute this command')
            return

        args.append(os.path.normpath(path))

        # Add the additional arguments from the settings file to the command
        for key, value in pref.additional_args.items():
            arg = key
            if value != "":
                arg += "=" + value
            args.append(arg)

        self.parse_report(args)

    def parse_report(self, args):
        report = self.shell_out(args)
        debug_message(report)
        lines = re.finditer('.*\((?P<number>\d+) fixable violations\)', report)

        for line in lines:
            error = CheckstyleError(0, line.group('number') + " fixed violations")
            self.error_list.append(error)


class PhpcbfTextBase(sublime_plugin.TextCommand):
    """Base class for Text commands in the plugin, mainly here to check php files"""
    description = ''

    def run(self, args):
        debug_message('Not implemented')

    def description(self):
        if not PhpcbfTextBase.should_execute(self.view):
            return "Invalid file format"
        else:
            return self.description

    @staticmethod
    def should_execute(view):
        if view.file_name() is not None:

            try:
                ext = os.path.splitext(view.file_name())[1]
                result = ext[1:] in pref.extensions_to_execute
            except:
                debug_message("Is 'extensions_to_execute' setup correctly")
                return False

            for block in pref.extensions_to_blacklist:
                match = re.search(block, view.file_name())
                if match is not None:
                    return False

            return result

        return False


class Phpcbf(PhpcbfTextBase):
    """Main plugin class for building the checkstyle report"""
    description = 'Fix coding standard issues (phpcbf)'

    # Class variable, stores the instances.
    instances = {}

    @staticmethod
    def instance(view, allow_new=True):
        '''Return the last-used instance for a given view.'''
        view_id = view.id()
        if view_id not in Phpcbf.instances:
            if not allow_new:
                return False
            Phpcbf.instances[view_id] = Phpcbf(view)
        return Phpcbf.instances[view_id]

    def __init__(self, view):
        self.view = view
        self.checkstyle_reports = []
        self.report = []
        self.event = None
        self.error_lines = {}
        self.error_list = []
        self.standards = []

    def fix_standards_errors(self, tool, path):
        self.error_lines = {}
        self.error_list = []
        self.report = []

        CodeBeautifier().get_errors(path)


class PhpcbfEventListener(sublime_plugin.EventListener):
    """Event listener for the plugin"""
    def on_post_save(self, view):
        if PhpcbfTextBase.should_execute(view):
            if pref.on_save is True:
                cmd = Phpcbf.instance(view)
                cmd.fix_standards_errors("CodeBeautifier", view.file_name())

    def on_selection_modified(self, view):
        if not PhpcbfTextBase.should_execute(view):
            return

    def on_pre_save(self, view):
        """ Project based settings, currently able to see an API based way of doing this! """
        if not PhpcbfTextBase.should_execute(view) or st_version == 2:
            return

        current_project_file = view.window().project_file_name()
        debug_message('Project files:')
        debug_message(' Current: ' + str(current_project_file))
        debug_message(' Last Known: ' + str(pref.project_file))

        if current_project_file is None:
            debug_message('No project file defined, therefore skipping reload')
            return

        if pref.project_file == current_project_file:
            debug_message('Project files are the same, skipping reload')
        else:
            debug_message('Project files have changed, commence the reload')
            pref.load()
            pref.project_file = current_project_file
