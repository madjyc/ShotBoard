import bisect
import json


DEFAULT_JSON_FILENAME = "shots.json"


class ShotBoardDb:
    def __init__(self):
        self._frame_count = 0
        self._shots = [] # contains the start frame number of each shot (integer)
        self._is_dirty = False


    def is_dirty(self):
        return self._is_dirty


    def clear_shots(self):
        self._shots.clear()
        self._is_dirty = True


    def set_shot_count(self, frame_count):
        self._frame_count = frame_count
        self._is_dirty = True


    def get_frame_count(self):
        return self._frame_count


    def set_shots(self, frame_count, frames):
        self.set_shot_count(frame_count)
        self._shots = sorted(list(set(frames)))
        self._is_dirty = True


    def add_shot(self, start_frame):
        index = bisect.bisect_left(self._shots, start_frame)
        self._shots.insert(index, start_frame)
        self._is_dirty = True
        return index


    def del_shot(self, frame):
        if frame in self._shots:
            self._shots.remove(frame)
            self._is_dirty = True


    def get_shot(self, shot_index):
        return self._shots.index(shot_index)
    

    def get_start_end_frame_indexes(self, frame_index):
        assert frame_index >= 0
        assert self._shots
        if not self._shots:
            return
        
        # The last frame doesn't have a next frame
        if frame_index >= self._shots[-1]:
            return self._shots[-1], self._frame_count
        
        shot_index = bisect.bisect_left(self._shots, frame_index)
        if self._shots[shot_index] == frame_index:
            start_index = shot_index
            end_index = shot_index + 1
        else:
            start_index = shot_index - 1
            end_index = shot_index

        start_frame = self._shots[start_index]
        end_frame = self._shots[end_index] if end_index < len(self._shots) else self._frame_count

        return start_frame, end_frame


    # Example usage: if start_frame in db:
    def __contains__(self, start_frame):
        return start_frame in self._shots


    # Example usage: del db[shot_index]
    def __delitem__(self, shot_index):
        del self._shots[shot_index]
        self._is_dirty = True


    # Example usage: len(db)
    def __len__(self):
        return len(self._shots)


    # Example usage: for frame in db; for index, frame in enumerate(db)
    def __iter__(self):
        for start_frame in self._shots:
            yield start_frame


    # Example usage: print(db):
    def __str__(self):
        return f"ShotBoardDb(frames={self._shots})"


    def save_to_json(self, filename=DEFAULT_JSON_FILENAME):
        data = {
            "frame_count": self._frame_count,
            "shots": self._shots
        }
        with open(filename, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file)
        self._is_dirty = False


    def load_from_json(self, filename=DEFAULT_JSON_FILENAME):
        self.clear_shots()
        try:
            with open(filename, 'r', encoding='utf-8') as json_file:
                data = json.load(json_file)
                self.set_shots(data["frame_count"], data["shots"])
        except FileNotFoundError:
            print(f"File not found: {filename}")
        except json.JSONDecodeError as e:
            print(f"Error loading JSON: {e}")
        except Exception as e:
            print(f"An error occurred while loading data: {e}")
        self._is_dirty = False


# TEST
if __name__ == "__main__":
    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
