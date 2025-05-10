import hassapi as hass
import datetime
import tibber
import pandas as pd
import numpy as np
from matplotlib import pyplot as plt
import seaborn as sns

from aiohttp.client_exceptions import ClientConnectorError

"""
App that creates a plot of the current and future electricity prices

Arguments
---------

tibber_api_token : The Tibber API token to get the electricity prices
quantile_markers : Dictionary of `quantile : kwargs` to show in the plot as axhline
price_level_helper : Name of input number entity to store current price level compared to the next 12 hours
min_max_price : If the max price of over the next twelve hours is below this value, assume this value for price level calculation.
low_price_wait_helper : Namer of input number entity to store how many hours to wait for the next low-price period within the next 12 hours
extra_plots : Dictionary of `variable_name : kwargs` of time series in glibal vars to show
extra_ylabel : Label for the y axis of the extra plots
save_plot : path and filename of the ploit to be saved

"""


class TibberPricePlot(hass.Hass):
    async def initialize(self):
        # Create tibber API object
        self.tibber_connection = tibber.Tibber(self.args["tibber_api_token"], user_agent="myAppDaemonApp")
        self.quantile_markers = self.args.get("quantile_markers", {})
        self.extra_plots = self.args.get("extra_plots", {})
        self.extra_ylabel = self.args.get("extra_ylabel", "")
        self.price_level_helper = self.args.get("price_level_helper", "")
        self.min_max_price = float(self.args.get("min_max_price", 0.0))
        self.low_price_wait_helper = self.args.get("low_price_wait_helper", "")
        self.save_plot = self.args.get("save_plot", "/homeassistant/www/plots/prices.png")
        
        connected = False
        while not connected:
            try:
                await self.tibber_connection.update_info()
                self.home = self.tibber_connection.get_homes()[0]
                await self.home.update_info()
                connected = True
            except ClientConnectorError:
                await self.sleep(600)
        # Update the price info now and once every hour
        start = datetime.time(minute=1)
        self.price_data = None
        await self.update_price_data({})
        self.run_hourly(self.update_price_data, start)

        # Make a new plot every 5 minutes
        every = 5 * 60
        self.run_every(self.make_plot, "now", every)

    async def terminate(self):
        await self.tibber_connection.close_connection()

    async def update_price_data(self, kwargs):
        """Update the hourly electricity prices of previous days, today and possibly tomorrow."""

        try:
            await self.home.update_price_info()
        except ClientConnectorError:
            self.log("Could not connect to Tibber API.")
            return
            
        # Get the dict of prices; keys are start timeas as strings
        price_dict = self.home.price_total
        if len(price_dict) > 0 or self.price_data is None:
            self.price_data = pd.Series(price_dict)
        # Turn string index to datetime objects
        self.price_data.rename(pd.to_datetime, inplace=True)

        # Publish the price data as a global variable
        self.global_vars["electricity_prices"] = self.price_data.copy()

    async def make_plot(self, kwargs):
        """Make the plot and store it in the configured location."""
        data = self.price_data
        if len(data) == 0:
            # No data?
            self.log("No price data available.")
            return

        # Convert the time series to a data frame, because that magically makes the plot work
        df = pd.DataFrame({"datetime": data.index, "price": data.array})
        tz = df["datetime"].iloc[-1].tz
        df["date"] = [dt.date() for dt in df["datetime"]]
        df["time"] = [dt.hour + (dt.minute / 60) for dt in df["datetime"]]
        # Insert dummy values to plot the last hour
        dummy = df.groupby('date').last().reset_index()
        dummy["time"] = 24
        df = pd.concat([df, dummy], ignore_index=True)
        
        # Filter out prvious days
        now = datetime.datetime.now(tz=data.index[0].tz)
        df = df[df["datetime"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]

        # Plot the plot
        fig, ax = plt.subplots()
        sns.lineplot(
            df, x="time", y="price", style="date", hue="date", drawstyle="steps-post"
        )
        # Add a vertical line at now
        now_time = now.hour + now.minute / 60.0
        ax.axvline(now_time, color="black", linestyle="dotted")

        # Add quantiles
        for quantile, args in self.quantile_markers.items():
            value = data.quantile(quantile)
            ax.axhline(value, **args)
    
        # Get current price as well as min max over the next 12 hours to calculate price level
        now_hour = now.replace(second=0, minute=0, microsecond=0)
        data12h = data[now_hour : now + datetime.timedelta(hours=12)]
        now_price = data12h.asof(now)
        min12h = data12h.min()
        max12h = max(data12h.max(), self.min_max_price)
        price_level = int(100. * (now_price - min12h) / (max12h - min12h))
        if self.price_level_helper:
            self.set_value(self.price_level_helper, price_level)

        # Get next low-price time
        kernel = np.array([1. ,1.])
        average = np.convolve(data12h.to_numpy(), kernel, mode="valid")
        low_time = data12h.index[np.argmin(average)]
        wait_hours = (low_time - now).total_seconds() / 3600.
        if wait_hours < 0:
            wait_hours = 0
        if self.low_price_wait_helper:
            self.set_value(self.low_price_wait_helper, f"{wait_hours:.1f}")

        # Add extra plots
        if len(self.extra_plots):
            ax2 = ax.twinx()
            for varname, args in self.extra_plots.items():
                series = self.global_vars.get(varname, None)
                if series is None:
                    self.log(f"Could not load {varname} from global variables.")
                    continue
                df = pd.DataFrame({"datetime": series.index, "value": series.array})
                df["datetime"] = [dt.astimezone(tz) for dt in df["datetime"]]
                df["date"] = df["datetime"].dt.date
                df["time"] = df["datetime"].dt.hour + (df["datetime"].dt.minute / 60)
                # Insert dummy values to plot the last hour
                dummy = df[df["time"] == 0].copy()
                dummy["time"] += 24
                dummy["date"] += pd.Timedelta(days=-1)
                df = pd.concat([df, dummy], ignore_index=True)
                sns.lineplot(df, ax=ax2, x="time", y="value", **args)
            ax2.set_ylabel(self.extra_ylabel)

        # Make things a bit prettier
        ax.set_xlim(left=0, right=24.01)
        tick_vals = list(range(0, 25, 3))
        tick_labels = ["%02d:00" % t for t in range(0, 25, 3)]
        ax.set_xticks(tick_vals, tick_labels)
        ax.set_ylabel("electricity price / [€/kWh]")
        ax.set_ylim(bottom=0.)
        if len(self.extra_plots):
            ax2.set_ylim(bottom=0., top=101.)
        fig.tight_layout()
        # Save to HA's static webserver
        fig.savefig(self.save_plot)
        plt.close()
