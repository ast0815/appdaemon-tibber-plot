import hassapi as hass
import datetime
import tibber
import pandas as pd
from matplotlib import pyplot as plt
import seaborn as sns

"""
App that creates a plot of the current and future electricity prices

Arguments
---------

tibber_api_token : The Tibber API token to get the electricity prices
quantile_markers : Dictionary of `quantile : kwargs` to show in the plot as axhline
extra_plots : Dictionary of `variable_name : kwargs` of time series in glibal vars to show
extra_ylabel : Label for the y axis of the extra plots

"""


class TibberPricePlot(hass.Hass):
    async def initialize(self):
        # Create tibber API object
        self.tibber_connection = tibber.Tibber(self.args["tibber_api_token"])
        self.quantile_markers = self.args.get("quantile_markers", {})
        self.extra_plots = self.args.get("extra_plots", {})
        self.extra_ylabel = self.args.get("extra_ylabel", "")
        await self.tibber_connection.update_info()
        self.home = self.tibber_connection.get_homes()[0]
        await self.home.update_info()

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
        """Update the hourly electricity prices of today and possibly tomorrow."""

        await self.home.update_price_info()
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
        df["date"] = [dt.date() for dt in df["datetime"]]
        df["time"] = [dt.hour for dt in df["datetime"]]
        # Insert dummy values to plot the last hour
        dummy = df[df["time"] == 23].copy()
        dummy["time"] = 24
        df = pd.concat([df, dummy], ignore_index=True)

        # Plot the plot
        fig, ax = plt.subplots()
        sns.lineplot(
            df, x="time", y="price", style="date", hue="date", drawstyle="steps-post"
        )
        # Add a vertical line at now
        now = datetime.datetime.now()
        now_time = now.hour + now.minute / 60.0
        ax.axvline(now_time, color="black", linestyle="dotted")

        # Add quantiles
        for quantile, args in self.quantile_markers.items():
            value = data.quantile(quantile)
            ax.axhline(value, **args)

        # Add extra plots
        if len(self.extra_plots):
            ax2 = ax.twinx()
            for varname, args in self.extra_plots.items():
                series = self.global_vars.get(varname, None)
                if series is None:
                    self.log(f"Could not load {varname} from global variables.")
                    continue
                df = pd.DataFrame({"datetime": series.index, "value": series.array})
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
        fig.tight_layout()
        # Save to HA's static webserver
        fig.savefig(self.config_dir + "/../www/plots/prices.png")
        plt.close()
