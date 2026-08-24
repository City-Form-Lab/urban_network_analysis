import time
import sys
# import pandas

class Logger():

    """

    verbosity levels:
        0: no output   
        1: only major events (default)
        2: major events + details

    """

    def __init__(self, verbosity:int=1) -> None:
        self.start_time = time.perf_counter_ns()
        self.total_time = 0
        self.log_list = []
        self.verbosity = verbosity

        self.log('initialize', f"SIMULATION STARTED", v=2)
        self.log('initialize', f"{sys.version}", v=2)
        #self.log('initialize', f"Dependencies: Geopandas:{gpd.__version__}, Numpy:{numpy.__version__}, NetworkX:{numba.__version__}", v=2)

    def log(self, event: str, details:str="", v:int=0):
        log_time = time.perf_counter_ns()

        # if (len(self.log_list) == 0) and (self.verbosity > 0):
        #     print(f"{'total time':^10s} | {'seconds elapsed':^15s} | event")

        time_elapsed = log_time - self.start_time
        self.total_time += time_elapsed

        log_entry = {
            "seconds_elapsed": time_elapsed/1e9,
            "cumulative_seconds": self.total_time/1e9,
            'event': event, 
            'details': details,
        }

        # TODO: consider preallocation to avoid memory impact of append
        self.log_list.append(log_entry)


        #self.log_df = pd.concat([self.log_df, pd.DataFrame(log_entry)] ,ignore_index=True)
        if self.verbosity > 0 and self.verbosity >= v:
            print(
                f"{self.total_time/1e9:10.4f} | "
                f"{time_elapsed/1e9:15.6f} | "
                f"{event} | "
                f"{details}"
            )
        self.start_time = time.perf_counter_ns()

    def warn(self, warning_text):
        return 

    # @property
    # def log_df(self):
    #     return pandas.DataFrame(self.log_list)
