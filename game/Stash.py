from game.BeliefState import BeliefState
from game.History import History
from utils.util import *
from utils.history_utils import *
import threading

class Stash:
    def __init__(self, color):
        self.levels = dict()
        self.lock = threading.BoundedSemaphore()
        # self.secondaryLock = threading.BoundedSemaphore()
        self.color = color
        self.history = History()
        self.kill = False

    def __len__(self):
        boards = 0
        for level in self.levels:
            for phase in self.levels[level]:
                boards += len(self.levels[level][phase])
        return boards

    def __str__(self):
        print("Stash:")
        for level in self.levels:
            for phase in self.levels[level]:
                print(level, phase)
                print(len(self.levels[level][phase]))
        return ''

    def _background_improvements(self):
        print("Starting background processor...")
        while not self.kill:
            acquired = self.lock.acquire()
            assert acquired
            self.improve_stash(maxAtATime=10, background=True)
            self.lock.release()
            time.sleep(.25)

    def start_background_processor(self):
        thread = threading.Thread(target=self._background_improvements, name="processor")
        thread.start()

    def end_background_processor(self):
        self.kill = True

    ##TODO: Avoid stashing boards with pieces in starting positions (for bots that pass a lot right away)
    ##TODO: Also take copy of best board (even if it becomes impossible later, we can still revive it for HelperBot)
    def stash_boards(self, phase : Phase, turn : int, beliefState : BeliefState, maxInDist : int):
        self.lock.acquire()
        boardsToKeep = list(sorted(beliefState.myBoardDist, key = beliefState.myBoardDist.get, reverse=True))[:maxInDist]
        boardsToStash = set(beliefState.myBoardDist).difference(boardsToKeep)
        for board in boardsToStash:
            beliefState.oppBoardDists.pop(board)
            beliefState.myBoardDist.pop(board)
        if len(beliefState.myBoardDist) > 0:
            normalize(beliefState.myBoardDist, adjust=True)
        if turn not in self.levels: self.levels[turn] = dict() 
        self.levels[turn][phase] = self.get_boards_at_phase(phase, turn) + list(boardsToStash)
        
        bonusPhase, bonusTurn = get_next_phase_and_turn(phase, turn)
        if bonusTurn not in self.levels: self.levels[bonusTurn] = dict()
        if bonusPhase not in self.levels[bonusTurn]: self.levels[bonusTurn][bonusPhase] = []
        
        print(f"Stashed {len(boardsToStash)} boards")

        self.lock.release()

    def add_history(self, turn : int, phase : Phase, result):
        self.lock.acquire()
        self.history.add_history(turn, phase, result)
        self.lock.release()

    def stash_exists_at_phase(self, phase : Phase, turn : int):
        return turn in self.levels and phase in self.levels[turn]

    def get_boards_at_phase(self, phase : Phase, turn : int):
        if self.stash_exists_at_phase(phase, turn):
            return self.levels[turn][phase]
        else:
            return []

    def latest_phase_with_boards(self, excludeLast = False):
        if len(self) == 0:
            return None, None

        phase = Phase(0)
        turn = 0
        while self.stash_exists_at_phase(phase, turn):
            phase, turn = get_next_phase_and_turn(phase, turn)
        phase, turn = get_prev_phase_and_turn(phase, turn)
        upToDateBoards = self.get_boards_at_phase(phase, turn)
        if len(upToDateBoards) == len(self):
            return None, None
        if excludeLast:
            phase, turn = get_prev_phase_and_turn(phase, turn)
        while len(self.get_boards_at_phase(phase, turn)) == 0:
            phase, turn = get_prev_phase_and_turn(phase, turn)
        return phase, turn

    '''
    Warning: you must acquire a lock before calling this function.
    '''
    def improve_stash(self, maxAtATime=600, background = False):
        # if not background: print(f"Improving stash up to {maxAtATime} boards at a time...")

        beliefState = BeliefState(self.color)
        beliefState.myBoardDist = {}
        beliefState.oppBoardDists = {}
        beliefState.catchingUp = True

        phase, turn = self.latest_phase_with_boards(excludeLast=True)
        if phase == None:
            if not background: print("Stash has no more boards requiring expansion!")
            return
        boardsToAdvance = set(self.get_boards_at_phase(phase, turn)[:maxAtATime])
        beliefState.myBoardDist = {b : 1/len(boardsToAdvance) for b in boardsToAdvance}
        beliefState.oppBoardDists = {b : {b : 1} for b in boardsToAdvance}

        assert self.history.has_history(phase, turn)

        try:
            self.history.apply_history(beliefState, phase, turn)
        except EmptyBoardDist:
            beliefState.myBoardDist = dict()
            beliefState.oppBoardDists = dict()

        beliefState._check_invariants()
        
        nextPhase, nextTurn = get_next_phase_and_turn(phase, turn)
        self.levels[nextTurn][nextPhase] += list(sorted(beliefState.myBoardDist, key=beliefState.myBoardDist.get, reverse=True))
        self.levels[turn][phase] = list(set(self.levels[turn][phase]).difference(set(boardsToAdvance)))

        if (nextPhase, nextTurn) == self.history.get_future_phase_and_turn() and len(beliefState.myBoardDist) > 0:
            print(f"Found {len(beliefState.myBoardDist)} new possible boards!")
            print(self)
        else:
            print(f"Moved {len(boardsToAdvance)} boards from turn {turn} phase {phase} into {len(beliefState.myBoardDist)} in turn {nextTurn} phase {nextPhase}")
            # currentPhase, currentTurn = self.history.get_current_phase_and_turn()
            # print(f"Currently on turn {currentTurn}, phase {currentPhase}")

    '''
    ##TODO: ADD TIME LIMIT!
    Note: adds in boards with prob 0
    '''
    def add_possible_boards(self, beliefState : BeliefState, numBoards : int, urgent = True):
        assert len(self) > 0, "No boards remaining in stash!"
        self.lock.acquire()
        phase, turn = self.history.get_future_phase_and_turn()
        if urgent:
            while len(self.get_boards_at_phase(phase, turn)) == 0:
                # print("No up-to-date boards found, improving stash...")
                # print(self)
                self.improve_stash()
        else:
            if len(self.get_boards_at_phase(phase, turn)) == 0:
                self.lock.release()
                return

        numAdditionalBoards = max(0, numBoards - len(beliefState.myBoardDist))
        possibleBoards = self.levels[turn][phase][:numAdditionalBoards]
        self.levels[turn][phase] = self.levels[turn][phase][numAdditionalBoards:]

        self.lock.release()

        for board in possibleBoards:
            if board not in beliefState.myBoardDist:
                beliefState.myBoardDist[board] = 0
                beliefState.oppBoardDists[board] = {board: 1}
        normalize(beliefState.myBoardDist, adjust=True)

        return possibleBoards

