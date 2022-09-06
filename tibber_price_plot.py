import hassapi as hass
import datetime
import tibber
import pandas as pd
from matplotlib import pyplot as plt
import seaborn as sns

"""
App that creates a plot of the current and future electricity prices

"""


class TibberPricePlot(hass.Hass):
    async def initialize(self):
        # Create tibber API object
        self.tibber_connection = tibber.Tibber(self.args["tibber_api_token"])
        await self.tibber_connection.update_info()
        self.home = self.tibber_connection.get_homes()[0]
        await self.home.update_info()

        # Update the price info now and once every hour
        start = datetime.time(minute=1)
        await self.update_price_data({})
        self.run_hourly(self.update_price_data, start)

        # Make a new plot every 5 minutes
        every = 5 * 60
        self.run_every(self.make_plot, "now", every)

    async def terminate(self):
        await self.tibber_connection.close_connection()

    async def update_price_data(self, kwargs):
        """Update the hourly electricity prices of today and possibly tomorrow."""

        await self.home.update_price_info()
        # Get the dict of prices; keys are start timeas as strings
        price_dict = self.home.price_total
        self.price_data = pd.Series(price_dict)
        # Turn string index to datetime objects
        self.price_data.rename(pd.to_datetime, inplace=True)

    async def make_plot(self, kwargs):
        """Make the plot and store it in the configured location."""
        data = self.price_data
        # Convert the time series to a data frame, because that magically makes the plot work
        df = pd.DataFrame({"datetime": data.index, "price": data.array})
        df["date"] = df["datetime"].dt.date
        df["time"] = df["datetime"].dt.hour
        # Insert dummy values to plot the last hour
        dummy = df[df["time"] == 23]
        dummy["time"] = 24
        df = pd.concat([df, dummy], ignore_index=True)
        self.log(df)

        # Plot the plot
        fig, ax = plt.subplots()
        sns.lineplot(df, x="time", y="price", style="date", hue="date", drawstyle="steps-post")
        # Add a vertical line at now
        now = datetime.datetime.now()
        now_time = now.hour + now.minute / 60.
        ax.axvline(now_time, color="black", linestyle="dotted")

        # Make things a bit prettier
        ax.set_xlim(left=0, right=24.01)
        tick_vals = list(range(0, 25, 3))
        tick_labels = [ "%02d:00"%t for t in range(0, 25, 3) ]
        ax.set_xticks(tick_vals, tick_labels)
        ax.set_ylabel("electricity price / [€/kWh]")
        fig.tight_layout()
        # Save to HA's static webserver
        fig.savefig(self.config_dir + "/../www/plots/prices.png")
        plt.close()
