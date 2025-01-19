import bisect
import json


DEFAULT_JSON_FILENAME = "shots.json"


class ShotBoardDb:
    # _frames

    def __init__(self):
        self._frame_count = 0
        self._frames = [] # contains the start frame number of each shot


    # Example usage: db.clear()
    def clear(self):
        self._frames.clear()


    def set_frame_count(self, frame_count):
        self._frame_count = frame_count


    def get_frame_count(self):
        return self._frame_count


    def set_frames(self, frame_count, frames):
        self.set_frame_count(frame_count)
        self._frames = sorted(list(set(frames)))


    def add_frame(self, frame):
        index = bisect.bisect_left(self._frames, frame)
        self._frames.insert(index, frame)
        return index


    def del_frame(self, frame):
        if frame in self._frames:
            self._frames.remove(frame)


    def get_frame_index(self, frame):
        return self._frames.index(frame)
    

    def get_start_end_frames(self, frame):
        assert frame >= 0
        assert self._frames
        if not self._frames:
            return
        
        # The last frame doesn't have a next frame
        if frame >= self._frames[-1]:
            return self._frames[-1], self._frame_count
        
        index = bisect.bisect_left(self._frames, frame)
        if self._frames[index] == frame:
            start_index = index
            end_index = index + 1
        else:
            start_index = index - 1
            end_index = index

        start_frame = self._frames[start_index]
        end_frame = self._frames[end_index] if end_index < len(self._frames) else self._frame_count

        return start_frame, end_frame


    # Example usage: if frame in db:
    def __contains__(self, frame):
        return frame in self._frames


    # Example usage: del db[index]
    def __delitem__(self, index):
        del self._frames[index]


    # Example usage: len(db)
    def __len__(self):
        return len(self._frames)


    # Example usage: for movie_name, clip in db
    def __iter__(self):
        for frame in self._frames:
            yield frame


    # Example usage: print(db):
    def __str__(self):
        return f"ShotBoardDb(positions={self._frames})"


    def save_to_json(self, filename=DEFAULT_JSON_FILENAME):
        data = {
            "frame_count": self._frame_count,
            "frames": self._frames
        }
        with open(filename, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file)


    def load_from_json(self, filename=DEFAULT_JSON_FILENAME):
        self.clear()
        try:
            with open(filename, 'r', encoding='utf-8') as json_file:
                data = json.load(json_file)
                self.set_frames(data["frame_count"], data["frames"])
        except FileNotFoundError:
            print(f"File not found: {filename}")
        except json.JSONDecodeError as e:
            print(f"Error loading JSON: {e}")
        except Exception as e:
            print(f"An error occurred while loading data: {e}")


# TEST
if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
