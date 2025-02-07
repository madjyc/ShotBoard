
class Command:
    def __init__(self):
        self._undo_func = None
        self._redo_func = None
        self._undo_data = None
        self._redo_data = None


    def set_undo(self, func, data):
        self._undo_func = func
        self._undo_data = data


    def set_redo(self, func, data):
        self._redo_func = func
        self._redo_data = data


    def undo(self):
        # assert self._owner, "Error - Undefined undo owner"
        assert self._undo_func, "Error - Undefined undo fonction"
        self._undo_func(self._undo_data)


    def redo(self):
        # assert self._owner, "Error - Undefined undo owner"
        assert self._redo_func, "Error - Undefined redo fonction"
        self._redo_func(self._redo_data)


class CommandHistory():
    MAX_COMMANDS = 100

    def __init__(self):
        self.clear()


    def clear(self):
        self._list = []
        self._index = -1


    def push(self, cmd):
        # When a new command is pushed, clear the redo stack and append the new command.
        while len(self._list) >= CommandHistory.MAX_COMMANDS:
            del self._list[0]
            self._index -= 1
        self._list = self._list[:self._index + 1]
        self._list.append(cmd)
        self.redo()


    def undo(self):
        print(">>> UNDO <<<")
        # Execute the undo method of the current command and move the index back.
        if self._index >= 0:
            self._list[self._index].undo()
            self._index -= 1


    def redo(self):
        print(">>> REDO <<<")
        # Increment the index and execute the redo method of the next command.
        if self._index < len(self._list) - 1:
            self._index += 1
            self._list[self._index].redo()


# TEST
if __name__ == "__main__":
    # Example of using the Command class
    def add_item(data):
        data['list'].append(data['item'])

    def remove_item(data):
        data['list'].remove(data['item'])

    cmd_history = CommandHistory()
    my_list = []

    # Create instances of Command
    cmd1 = Command()
    cmd1.set_redo(func=add_item, data={'list': my_list, 'item': 'Item 1'})
    cmd1.set_undo(func=remove_item, data={'list': my_list, 'item': 'Item 1'})

    cmd2 = Command()
    cmd2.set_redo(func=add_item, data={'list': my_list, 'item': 'Item 2'})
    cmd2.set_undo(func=remove_item, data={'list': my_list, 'item': 'Item 2'})

    cmd3 = Command()
    cmd3.set_redo(func=add_item, data={'list': my_list, 'item': 'Item 3'})
    cmd3.set_undo(func=remove_item, data={'list': my_list, 'item': 'Item 3'})

    cmd_history.push(cmd1)
    cmd_history.push(cmd2)

    cmd_history.undo()

    cmd_history.push(cmd3)
    cmd_history.undo()

    print(f"\033[91mTHIS MODULE FILE IS NOT MEANT TO BE RUN!\033[0m")
