import hassapi as hass
import datetime

"""
App that creates a plot of the current and future electricity prices

"""

class TibberPricePlot(hass.Hass):

    def initialize(self):
        # Make a new plot every 5 minutes
        every = 5 * 60
        self.run_every(self.make_plot, "now", every)

    def make_plot(self, kwargs):
        """Make the plot and store it in the configured location."""
        self.log("TIBBERPLOT!!")
