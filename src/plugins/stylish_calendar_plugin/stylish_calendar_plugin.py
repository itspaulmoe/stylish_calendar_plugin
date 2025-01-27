import os
import urllib.request
import locale
import logging
import json
from datetime import datetime, timedelta

import openai
from openai import OpenAI
import requests
import textwrap
from icalevents.icalevents import events
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

class StylishCalendarPlugin(BasePlugin):
    def __init__(self, config, **dependencies):
        # Pass them on to BasePlugin
        super().__init__(config, **dependencies)

        # Then do your custom initialization:
        self.name = "Stylish Calendar Plugin"
        self.version = "1.0.0"
        self.description = "Fetches iCal data, displays daily calendar, optional ChatGPT summary"
        self.author = "Paul"

        # Attempt to set German locale (optional)
        try:
            locale.setlocale(locale.LC_TIME, 'de_DE.UTF-8')
        except locale.Error as e:
            logger.warning(f"Konnte de_DE.UTF-8 nicht setzen: {e}")

    CONFIG_FILE = "/home/inkypi/InkyPi/src/plugins/stylish_calendar_plugin/stylish_calendar_config.json"  # File to store configuration

    def save_config(self, config):
        """Saves the configuration to a file."""
        logger.debug(f"Saving configuration to {self.CONFIG_FILE}")
        try:
            os.makedirs(os.path.dirname(self.CONFIG_FILE), exist_ok=True)  # Ensure the directory exists
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(config, f)
            logger.info(f"Configuration saved to {self.CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            
    def load_config(self):
        """Loads the configuration from a file."""
        try:
            with open(self.CONFIG_FILE, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {self.CONFIG_FILE}")
            return {}
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return {}
        
    def generate_settings_template(self):
        """Pass the saved configuration to the settings template."""
        template_params = super().generate_settings_template()
        saved_config = self.load_config()
        template_params["ical_url"] = saved_config.get("ical_url", "")
        template_params["openai_api_key"] = saved_config.get("openai_api_key", "")
        template_params["use_chatgpt"] = saved_config.get("use_chatgpt", "off")
        return template_params

    def generate_image(self, settings, device_config):
        """
        The main function InkyPi calls to get the final PIL.Image.

        :param settings: dict from your settings.html form inputs
        :param device_config: the device config (for resolution, env keys, etc.)
        :return: A PIL.Image with the stylish calendar
        """
        # Load the persisted configuration
        config = self.load_config()

        # Get the latest settings from the form or use saved values
        ical_url = settings.get("ical_url", config.get("ical_url"))
        openai_api_key = settings.get("openai_api_key", config.get("openai_api_key"))
        use_chatgpt = settings.get("use_chatgpt", config.get("use_chatgpt", "off"))

        # Save the updated configuration
        self.save_config({
            "ical_url": ical_url,
            "openai_api_key": openai_api_key,
            "use_chatgpt": use_chatgpt
        })


        # If some fields are required, validate them:
        if not ical_url:
            raise RuntimeError("Fehlende iCal-URL (ical_url). Bitte in den Einstellungen angeben.")

        # 2) Determine display resolution from device_config
        #    e.g. (800, 480)
        resolution = device_config.get_resolution()
        width, height = resolution

        # 3) Fetch iCal events
        now = datetime.now()
        try:
            cal_events = events(url=ical_url,
                                start=now - timedelta(days=1),
                                end=now + timedelta(days=7))
        except Exception as e:
            logger.error(f"Failed to fetch/parse iCal: {e}")
            raise RuntimeError("Fehler beim Abrufen/Verarbeiten der iCal-URL. Bitte prüfen.")

        # 4) Separate events into "today" and "next 3 days"
        today_events, upcoming_events = self.split_events(cal_events)

        # 5) (Optional) Generate ChatGPT summary
        summary_text = None
        if use_chatgpt == "on":
            if not openai_api_key:
                raise RuntimeError("OpenAI API-Key ist benötigt, aber nicht vorhanden.")
            summary_text = self.generate_summary_with_chatgpt(upcoming_events, openai_api_key)

        # 6) Render the calendar image
        image = self.render_calendar_image(width, height, today_events, upcoming_events, summary_text) #add frame_style if necessary

        # 7) Return the final PIL image
        return image

    def split_events(self, cal_events):
        """
        Splits the iCal events into "today" vs. next 3 days.
        """
        today = datetime.now().date()
        three_days_later = today + timedelta(days=3)

        today_events = []
        upcoming_events = []

        for event in cal_events:
            event_date = event.start.date()
            if event_date == today:
                today_events.append(event)
            elif today < event_date <= three_days_later:
                upcoming_events.append(event)

        # Sort by start time
        today_events.sort(key=lambda e: e.start)
        upcoming_events.sort(key=lambda e: e.start)
        return today_events, upcoming_events

    def generate_summary_with_chatgpt(self, upcoming_events, api_key):
        """
        Calls OpenAI ChatGPT to generate a short summary in German.
        """
        client = OpenAI(api_key=api_key)
        #openai.api_key = api_key

        # Build a textual list of upcoming events
        event_lines = []
        for e in upcoming_events:
            date_str = e.start.strftime("%Y-%m-%d")
            time_str = e.start.strftime("%H:%M")
            event_lines.append(f"{date_str}, {time_str}, {e.summary}")

        prompt_text = (
            "Bitte erstelle eine kurze, umgangssprachliche Zusammenfassung "
            "der folgenden Termine. Die Zusammenfassung sollte insgesamt nicht mehr als 400 Zeichen umfassen. "
            "Beispiel:\n"
            "\"Morgen hast du um 15:00 Uhr einen Zahnarzttermin. Danach um 18:00 Uhr Klavierunterricht.\"\n"
            + "\n".join(event_lines)
        )

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini", #Replace with your valid model name
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.7,
                max_tokens=150,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to get ChatGPT summary: {e}")
            raise RuntimeError("Fehler beim Zugriff auf OpenAI. Bitte API-Key prüfen oder erneut versuchen.")

    def render_calendar_image(self, width, height, today_events, upcoming_events, summary_text): #add frame_style if neccesary
        """
        Renders a new (width x height) image with:
          - "Heutige Termine" on the left
          - "Nächste 3 Tage" (or ChatGPT summary) on the right
          - Optional 'frame_style' around the image (if desired)
        """
        # Basic color definitions
        WHITE   = (255, 255, 255)
        BLACK   = (0, 0, 0)
        RED     = (255, 0, 0)
        BLUE    = (0, 0, 255)
        GREEN   = (0, 255, 0)
        YELLOW  = (255, 255, 0)
        ORANGE  = (255, 165, 0)

        image = Image.new("RGB", (width, height), WHITE)
        draw = ImageDraw.Draw(image)

        # Optionally draw a frame if frame_style != "None"
        # if frame_style != "None":
        #     self.draw_frame(frame_style, draw, width, height, BLACK)

        # Load fonts
        try:
            font_headline = ImageFont.truetype("/usr/share/fonts/truetype/quicksand/Quicksand-Bold.ttf", 32)
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/quicksand/Quicksand-Medium.ttf", 24)
            font_title_bold = ImageFont.truetype("/usr/share/fonts/truetype/quicksand/Quicksand-Bold.ttf", 24)
            font_title_regular = ImageFont.truetype("/usr/share/fonts/truetype/quicksand/Quicksand-Medium.ttf", 24)
            font_main = ImageFont.truetype("/usr/share/fonts/truetype/quicksand/Quicksand-Medium.ttf", 18)
        except:
            font_headline = ImageFont.load_default()
            font_title = ImageFont.load_default()
            font_main = ImageFont.load_default()

        # Draw the headline
        draw.text((20, 10), "Pauls Kalender", font=font_headline, fill=RED)

        # Add a horizontal line below "Pauls Kalender"
        draw.line([(20, 50), (width - 20, 50)], fill=BLACK, width=2)

        # Titles
        draw.text((20, 60), "Heutige Termine", font=font_title_bold, fill=BLACK)
        draw.text((width // 2 + 20, 60), "In den nächsten 3 Tagen", font=font_title_regular, fill=BLACK)

        # Add a vertical dividing line between "Heutige Termine" and "Nächste 3 Tage"
        # dividing_line_x = width // 2
        # draw.line([(dividing_line_x, 50), (dividing_line_x, height - 20)], fill=BLACK, width=2)

        left_x = 20
        right_x = width // 2 + 20  # Current right section start
        right_margin = 20         # Add a margin from the right edge
        y_left = 100
        y_right = 100

        # Event block parameters
        event_block_x = 20
        event_block_y = 100
        event_block_width = width // 2 - 40  # Half the screen width minus margins
        event_block_vspacing = 10

        # -- LEFT: Today's Events --
        if not today_events:
            draw.text((left_x, y_left), "Keine Termine für heute.", font=font_main, fill=GREEN)
        else:
            for ev in today_events:
                time_str = ev.start.strftime("%H:%M")
                event_text = f"{time_str} - {ev.summary}"
                
                # Wrap text and calculate dimensions
                lines = self.wrap_text(event_text, font_main, event_block_width - 20)
                text_height = font_main.getbbox("A")[3] - font_main.getbbox("A")[1]
                event_block_height = len(lines) * (text_height + 5) + 10

                # Draw a rounded rectangle
                self.draw_rounded_rectangle(draw,
                                            [event_block_x, event_block_y,
                                             event_block_x + event_block_width,
                                             event_block_y + event_block_height],
                                            radius=10, fill=BLUE)

                # Render text inside the rectangle
                y_text = event_block_y + 5
                for line in lines:
                    draw.text((event_block_x + 10, y_text), line, font=font_main, fill=WHITE)
                    y_text += text_height + 5
                event_block_y += event_block_height + event_block_vspacing
                

        # -- RIGHT: Summaries or upcoming events
        if summary_text:
            summary_block_x = width // 2 + 20
            summary_block_y = 100
            summary_block_width = width // 2 - 40
            lines = self.wrap_text(summary_text, font_main, summary_block_width - 20)
            text_height = font_main.getbbox("A")[3] - font_main.getbbox("A")[1]
            summary_block_height = len(lines) * (text_height + 5) + 20

            # Draw a rounded rectangle
            self.draw_rounded_rectangle(draw,
                                        [summary_block_x, summary_block_y,
                                         summary_block_x + summary_block_width,
                                         summary_block_y + summary_block_height],
                                        radius=10, fill=ORANGE)

            # Render text inside the rectangle
            y_text = summary_block_y + 10
            for line in lines:
                draw.text((summary_block_x + 10, y_text), line, font=font_main, fill=BLACK)
                y_text += text_height + 5

        else:
            current_date = None
            max_text_width = width - right_margin - 20 # Enforce margin from the right edge and ensure extra space
            for ev in upcoming_events:
                date_label = self.format_date_german_day_month(ev.start)  # Use a helper function
                time_str = ev.start.strftime("%H:%M")

                # Add a new date header if it's a new day
                if current_date != ev.start.date():
                    draw.text((right_x, y_right), date_label, font=font_main, fill=BLUE)
                    y_right += 30
                    current_date = ev.start.date()

                # Add the event details with wrapping
                line_text = f"{time_str} - {ev.summary}"

                # Calculate the character width based on the font and draw object
                #bbox = draw.textbbox((0, 0), line_text, font=font_main)
                bbox = draw.textbbox((0, 0), "A" * len(line_text), font=font_main)
                char_width = bbox[2] // len(line_text)  # Average width per character
                wrap_width = (max_text_width - right_x) // char_width
                
                # Wrap the text to fit the width
                wrapped_text = textwrap.fill(line_text, width=wrap_width)

                #render the wrapped text
                for line in wrapped_text.split("\n"):
                    draw.text((right_x + 20, y_right), line, font=font_main, fill=BLACK)
                    y_right += 30  # Add vertical spacing between lines
                #draw.text((right_x + 20, y_right), wrapped_text, font=font_main, fill=BLACK)
                #y_right += 30 * wrapped_text.count("\n") + 30  # Add extra spacing for wrapped lines
                

        # Display the current date at the bottom
        today = datetime.now()
        date_description = self.format_date_german(today)  # Use manual formatting
        draw.text((20, height - 50), date_description, font=font_main, fill=BLACK)

        return image
    
    def format_date_german(self, date):
        """
        Formats the date in German manually, bypassing locale issues.
        """
        days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        months = [
            "Januar", "Februar", "März", "April", "Mai", "Juni",
            "Juli", "August", "September", "Oktober", "November", "Dezember"
        ]
    
        day = days[date.weekday()]  # Get the day name
        month = months[date.month - 1]  # Get the month name

        return f"Heute ist {day}, der {date.day}. {month} {date.year}."
    
    def format_date_german_day_month(self, date):
        """
        Formats the day and month in German for use in the "next 3 days" section.
        """
        days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        months = [
            "Januar", "Februar", "März", "April", "Mai", "Juni",
            "Juli", "August", "September", "Oktober", "November", "Dezember"
        ]

        day_name = days[date.weekday()]  # Get the day name in German
        month_name = months[date.month - 1]  # Get the month name in German

        return f"{day_name}, {date.day}. {month_name}"

    def wrap_text(self, text, font, max_width):
        """
        Wraps text to fit within a specified width.
        """
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            if font.getbbox(test_line)[2] <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    def draw_rounded_rectangle(self, draw, box, radius, fill=None, outline=None, outline_width=2):
        """
        Draws a rounded rectangle.
        Args:
            draw: PIL.ImageDraw.Draw instance.
            box: List of [x0, y0, x1, y1].
            radius: Corner radius.
            fill: Fill color.
            outline: Outline color.
            outline_width: Outline thickness.
        """
        x0, y0, x1, y1 = box
        draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)  # Top/bottom bars
        draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)  # Left/right bars

        # Four corners
        draw.pieslice([x0, y0, x0 + 2 * radius, y0 + 2 * radius], 180, 270, fill=fill)
        draw.pieslice([x1 - 2 * radius, y0, x1, y0 + 2 * radius], 270, 360, fill=fill)
        draw.pieslice([x0, y1 - 2 * radius, x0 + 2 * radius, y1], 90, 180, fill=fill)
        draw.pieslice([x1 - 2 * radius, y1 - 2 * radius, x1, y1], 0, 90, fill=fill)

        if outline:
            draw.rectangle([x0 + radius, y0, x1 - radius, y1], outline=outline, width=outline_width)
            draw.rectangle([x0, y0 + radius, x1, y1 - radius], outline=outline, width=outline_width)
            draw.arc([x0, y0, x0 + 2 * radius, y0 + 2 * radius], 180, 270, fill=outline, width=outline_width)
            draw.arc([x1 - 2 * radius, y0, x1, y0 + 2 * radius], 270, 360, fill=outline, width=outline_width)
            draw.arc([x0, y1 - 2 * radius, x0 + 2 * radius, y1], 90, 180, fill=outline, width=outline_width)
            draw.arc([x1 - 2 * radius, y1 - 2 * radius, x1, y1], 0, 90, fill=outline, width=outline_width)

    def draw_multiline_text(self, draw, position, text, font, wrap_width, text_color=(0, 0, 0)):
        """
        Draws multiline text on an image, wrapping it to the specified width.
        """
        x, y = position
        words = text.split()
        lines = []
        current_line = ""

        for w in words:
            test_line = (current_line + " " + w).strip()
            text_width = font.getbbox(test_line)[2]  # Use getbbox to calculate the width
            if text_width <= wrap_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = w
        if current_line:
            lines.append(current_line)

        for line in lines:
            draw.text((x, y), line, font=font, fill=text_color)
            y += font.getbbox(line)[3] - font.getbbox(line)[1] + 4  # Adjust line height spacing
